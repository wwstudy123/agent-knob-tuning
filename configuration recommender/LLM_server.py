from flask import Flask, request, jsonify
import json
import re
import os,sys
import heapq
import random
from config_rank import sort_list
import configparser
from reflection_memory import ReflectionMemory
from surrogate import CanonicalConfigEncoder, SurrogateModel, load_history_jsonl
from search_space import SearchSpace

# Auto-detect: use Anthropic SDK if env vars are set, otherwise OpenAI SDK
_use_anthropic = bool(os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))
if _use_anthropic:
    import anthropic
else:
    from openai import OpenAI

config = configparser.ConfigParser()
config.read(os.environ.get('AGENTTUNE_CONFIG', './config.ini'))

file_path = config['workload analyzer']['output_file']
with open(file_path, "r") as f:
    workload_features = f.read().strip()  
database_kernel=config['knob selector']['database_kernel']
hardware=config['knob selector']['hardware']
database_scale=config['knob selector']['database_scale']

knob_list_path = config['range pruner']['output_file']
with open(knob_list_path,"r") as f:
    knobs = json.load(f)
knob_metadata = knobs
knobs = json.dumps(knob_metadata, indent=4)

# Initialize dynamic search space (P1.3)
search_space = SearchSpace(knob_metadata)

metric_path = config['configuration recommender']['metric_file']
with open(metric_path, "r") as f:
    inner_metrics = f.read().strip() 

db_metric=config['configuration recommender']['db_metric']

HISTORY_NUM = int(config['configuration recommender']['history_num'])
NODE_COUNT = int(config['configuration recommender']['node_count'])
LLM_SERVER_PORT = int(config['configuration recommender']['LLM_server_port'])
RECORD_DIR_NAME = config['configuration recommender'].get('record_dir', 'record').strip()
RECORD_DIR = os.path.join('./configuration recommender', RECORD_DIR_NAME)
os.makedirs(RECORD_DIR, exist_ok=True)
REFLECTION_ENABLED = config.getboolean(
    'configuration recommender', 'reflection_enabled', fallback=False
)
REFLECTION_MAX_CHARS = config.getint(
    'configuration recommender', 'reflection_max_history_chars', fallback=8000
)
TOKEN_LOG_PATH = os.path.join(
    RECORD_DIR,
    config['configuration recommender'].get('token_log_path', 'token_usage.jsonl'),
)
STATIC_CONTEXT = {
    'knobs': knob_metadata,
    'workload_features': workload_features,
    'database_kernel': database_kernel,
    'hardware': hardware,
    'database_scale': database_scale,
}
reflection_memory = ReflectionMemory(
    STATIC_CONTEXT,
    token_budget=config.getint(
        'configuration recommender', 'token_budget', fallback=2_000_000
    ),
    summary_interval=config.getint(
        'configuration recommender', 'reflection_summary_interval', fallback=5
    ),
    summary_char_budget=REFLECTION_MAX_CHARS,
    recent_delta_limit=config.getint(
        'configuration recommender', 'reflection_recent_limit', fallback=5
    ),
    pinned_limit=config.getint(
        'configuration recommender', 'reflection_pinned_limit', fallback=6
    ),
)
SURROGATE_ENABLED = config.getboolean(
    'configuration recommender', 'surrogate_enabled', fallback=False
)
SURROGATE_MIN_SAMPLES = config.getint(
    'configuration recommender', 'surrogate_min_samples', fallback=12
)
SURROGATE_POOL_SIZE = config.getint(
    'configuration recommender', 'surrogate_candidate_pool', fallback=20
)
SURROGATE_BETA = config.getfloat(
    'configuration recommender', 'surrogate_beta', fallback=1.5
)
SURROGATE_FAILURE_THRESHOLD = config.getfloat(
    'configuration recommender', 'surrogate_failure_threshold', fallback=0.75
)
SURROGATE_EXPLORATION_SLOTS = config.getint(
    'configuration recommender', 'surrogate_exploration_slots', fallback=1
)
TOP_K = config.getint('configuration recommender', 'top_k', fallback=2)
candidate_metadata_path = config['knob selector']['candidate_knobs']
surrogate_encoder = CanonicalConfigEncoder(
    candidate_metadata_path,
    knob_list_path,
)


def _expand_candidate_pool(candidates, target_size, seed):
    """Create cheap local perturbations around LLM candidates."""
    if not candidates:
        return []
    rng = random.Random(seed)
    pool = []
    seen = set()

    def add(config_item):
        canonical = surrogate_encoder.canonicalize(config_item, include_missing=True)
        fingerprint = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
        if fingerprint not in seen:
            seen.add(fingerprint)
            pool.append(canonical)

    for candidate in candidates:
        add(candidate)
    attempts = 0
    while len(pool) < target_size and attempts < target_size * 20:
        attempts += 1
        base = dict(rng.choice(pool))
        knob_name = rng.choice(surrogate_encoder.knob_names)
        meta = surrogate_encoder.metadata[knob_name]
        if meta.get('type') == 'enum':
            values = surrogate_encoder.enum_values.get(knob_name) or []
            if values:
                base[knob_name] = rng.choice(values)
        else:
            lower = meta.get('min_value', meta.get('min', 0))
            upper = meta.get('max_value', meta.get('max', lower))
            step = meta.get('step') or 1
            try:
                lower, upper, step = int(lower), int(upper), max(1, int(step))
                current = int(base.get(knob_name, lower))
                radius = max(step, (upper - lower) // 10)
                proposed = current + rng.randint(-radius, radius)
                proposed = min(upper, max(lower, proposed))
                proposed = lower + round((proposed - lower) / step) * step
                base[knob_name] = min(upper, max(lower, proposed))
            except (TypeError, ValueError):
                continue
        add(base)
    return pool


def _surrogate_select(json_strings, request_number):
    """Pre-screen candidates; transparently fall back on sparse history."""
    diagnostics = {'enabled': SURROGATE_ENABLED, 'confidence': None}
    if not SURROGATE_ENABLED or not json_strings:
        return sort_list(json_strings), diagnostics
    candidates = []
    for value in json_strings:
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                candidates.append(parsed)
        except json.JSONDecodeError:
            continue
    history_path = os.path.join(RECORD_DIR, 'benmark_history')
    history = (
        load_history_jsonl(history_path, surrogate_encoder)
        if os.path.exists(history_path)
        else []
    )
    model = SurrogateModel(
        surrogate_encoder,
        min_samples=SURROGATE_MIN_SAMPLES,
        random_state=config.getint(
            'configuration recommender', 'random_seed', fallback=42
        ),
    ).fit(history)
    diagnostics.update(model.diagnostics())
    if len(history) < SURROGATE_MIN_SAMPLES:
        diagnostics['fallback_reason'] = 'insufficient_history'
        return sort_list(json_strings), diagnostics

    pool = _expand_candidate_pool(candidates, SURROGATE_POOL_SIZE, request_number)
    ranked = model.rank_candidates(
        pool,
        top_k=TOP_K,
        strategy='ucb',
        beta=SURROGATE_BETA,
        min_success_probability=max(0.0, 1.0 - SURROGATE_FAILURE_THRESHOLD),
        force_exploration=SURROGATE_EXPLORATION_SLOTS > 0,
    )
    selected = [entry['config'] for entry in ranked]
    if ranked:
        relative_uncertainty = [
            entry['prediction']['std']
            / max(1e-9, entry['prediction']['mean'] + entry['prediction']['std'])
            for entry in ranked
        ]
        diagnostics['confidence'] = max(
            0.0, min(1.0, 1.0 - sum(relative_uncertainty) / len(relative_uncertainty))
        )
        diagnostics['selected_predictions'] = ranked
    diagnostics['candidate_pool_size'] = len(pool)
    return selected or sort_list(json_strings), diagnostics


def extract_key_value_pairs(json_string):
    # match "key": value (numeric, possibly decimal)
    pattern = re.compile(r'"(\w+)":\s*([\d.]+)')
    matches = pattern.findall(json_string)
    data = {}
    for key, value in matches:
        try:
            data[key] = int(float(value))
        except (ValueError, TypeError):
            # Non-integer numeric (e.g. a fraction) is kept as float rather
            # than dropping the whole key-value pair.
            data[key] = float(value)
    return data

def convert_to_bytes(value):
    """Convert a byte-size spec to int bytes. Returns None if not a spec.

    Supports forms: ``16GB``, ``1.5GB``, ``0.75*16GB``, ``0.75x16GB``.
    """
    units = {
        'B': 1,
        'KB': 1024,
        'MB': 1024**2,
        'GB': 1024**3,
        'TB': 1024**4
    }
    if value is None:
        return None
    text = str(value).strip()
    m = re.match(
        r'^\s*(\d+(?:\.\d+)?)\s*(?:[*\u00d7]\s*(\d+(?:\.\d+)?)\s*)?([KMGT]B)\s*$',
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    base = float(m.group(1))
    factor = float(m.group(2)) if m.group(2) else 1.0
    unit = m.group(3).upper()
    return int(base * factor * units[unit])

def replace_units(json_string):
    def replace_match(match):
        converted = convert_to_bytes(match.group(0))
        # Leave the original substring untouched if it is not a byte spec,
        # so we never crash or silently drop valid tokens.
        return str(converted) if converted is not None else match.group(0)

    json_string = re.sub(
        r'\d+(?:\.\d+)?\s*(?:[*\u00d7]\s*\d+(?:\.\d+)?\s*)?[KMGT]B',
        replace_match,
        json_string,
        flags=re.IGNORECASE,
    )
    return json_string


def remove_comments(json_string):
    json_string = re.sub(r'//.*', '', json_string)
    json_string = re.sub(r'/\*.*?\*/', '', json_string, flags=re.DOTALL)
    json_string = re.sub(r',\s*}', '}', json_string)
    json_string = re.sub(r',\s*]', ']', json_string)
    return json_string

def _balanced_brace_blocks(text):
    """Return all top-level (depth-0) balanced ``{...}`` substrings.

    Tracks string literals and backslash escapes so braces inside keys,
    values or arrays do not break detection. Supports nested objects.
    """
    blocks = []
    depth = 0
    start = None
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    blocks.append(text[start:i + 1])
                    start = None
    return blocks


def extract_config_json(text):
    """Parse one or more knob JSON objects from LLM output, robustly.

    Strategy:
    1. Strip ```json / ``` code fences and remove comments.
    2. Replace byte-size units (e.g. ``2GB``) with integers.
    3. Try ``json.loads`` on the whole text first.
    4. Fallback: isolate balanced ``{...}`` blocks (nested-friendly) and
       parse each, keeping the dicts that decode successfully.
    5. Last resort: the legacy flat ``key: number`` extractor.

    Common envelopes such as ``{"recommendations": [...]}`` are unwrapped so
    each inner object is returned as a separate candidate.
    """
    if not text:
        return []
    cleaned = text
    fence = re.search(r'```(?:json)?\s*(.*?)\s*```', cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1)
    cleaned = remove_comments(cleaned)
    cleaned = replace_units(cleaned)

    raw = []
    try:
        obj = json.loads(cleaned)
        raw = obj if isinstance(obj, list) else [obj]
    except (json.JSONDecodeError, ValueError):
        for block in _balanced_brace_blocks(cleaned):
            try:
                obj = json.loads(block)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, (dict, list)):
                raw.append(obj)

    results = []
    for o in raw:
        if isinstance(o, list):
            results.extend(x for x in o if isinstance(x, dict))
        elif isinstance(o, dict):
            if isinstance(o.get('recommendations'), list):
                results.extend(
                    x for x in o['recommendations'] if isinstance(x, dict)
                )
            else:
                results.append(o)

    if not results:
        flat = extract_key_value_pairs(cleaned)
        if flat:
            results = [flat]
    return results


def call_open_source_llm(model, messages, filename):
    usage = {
        'prompt_tokens': 0,
        'completion_tokens': 0,
        'total_tokens': 0,
        'estimated': False,
    }
    if _use_anthropic:
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"),
            base_url=os.environ.get("ANTHROPIC_BASE_URL"),
        )
        system_prompt = messages[0]["content"]
        user_messages = messages[1:]

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=user_messages,
            temperature=1,
            top_p=0.98
        )
        # Find the text block (skip thinking blocks)
        content_text = ""
        for block in response.content:
            if block.type == "text":
                content_text = block.text
                break
        response_usage = getattr(response, 'usage', None)
        if response_usage:
            usage['prompt_tokens'] = int(getattr(response_usage, 'input_tokens', 0) or 0)
            usage['completion_tokens'] = int(getattr(response_usage, 'output_tokens', 0) or 0)
            usage['total_tokens'] = usage['prompt_tokens'] + usage['completion_tokens']
    else:
        client = OpenAI(
            api_key=config['knob selector']['api_key'],
            base_url=config['knob selector']['base_url']
        )
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=1,
            top_p=0.98
        )
        content_text = completion.choices[0].message.content
        response_usage = getattr(completion, 'usage', None)
        if response_usage:
            usage['prompt_tokens'] = int(getattr(response_usage, 'prompt_tokens', 0) or 0)
            usage['completion_tokens'] = int(getattr(response_usage, 'completion_tokens', 0) or 0)
            usage['total_tokens'] = int(
                getattr(response_usage, 'total_tokens', 0)
                or usage['prompt_tokens'] + usage['completion_tokens']
            )

    if usage['total_tokens'] == 0:
        # OpenAI-compatible providers do not always expose usage. Character
        # counts provide a conservative, explicitly labelled fallback.
        prompt_chars = sum(len(str(message.get('content', ''))) for message in messages)
        usage['prompt_tokens'] = max(1, prompt_chars // 4)
        usage['completion_tokens'] = max(1, len(content_text or '') // 4)
        usage['total_tokens'] = usage['prompt_tokens'] + usage['completion_tokens']
        usage['estimated'] = True

    configs = extract_config_json(content_text)
    print(f"parsed {len(configs)} config candidate(s) from LLM output")
    if not configs:
        print("No JSON configuration found in the input.")
        return usage

    existing = set()
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    existing.add(line)

    written = 0
    with open(filename, 'a', encoding='utf-8') as f:
        for cfg in configs:
            cfg_str = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
            if cfg_str in existing:
                continue
            f.write('\n')
            f.write(cfg_str)
            existing.add(cfg_str)
            written += 1

    if written:
        print(f"success recommendation! wrote {written} new config(s)")
    return usage

history_top = []
last_result = ""
app = Flask(__name__)
request_count = 0


@app.route('/restore', methods=['POST'])
def restore_state():
    """Restore LLM_server in-memory state from DB_client checkpoint."""
    global request_count, history_top, last_result, reflection_memory, search_space
    data = request.get_json() or {}
    request_count = int(data.get('request_count', 0))
    last_result = data.get('last_result', '') or ''
    restored = data.get('history_top', []) or []
    # history_top entries: [throughput, request_count, item]
    history_top = []
    for entry in restored:
        if isinstance(entry, (list, tuple)) and len(entry) >= 3:
            heapq.heappush(history_top, (entry[0], entry[1], entry[2]))
    restored_reflection = data.get('reflection_state')
    if restored_reflection:
        reflection_memory = ReflectionMemory.from_dict(restored_reflection)
    # Restore search space (P1.3)
    restored_search_space = data.get('search_space')
    if restored_search_space:
        try:
            search_space = SearchSpace.from_dict(restored_search_space)
            print(f'LLM_server restored search_space')
        except Exception as e:
            print(f'Warning: failed to restore search_space: {e}')
    print(f'LLM_server restored: request_count={request_count}, history_top={len(history_top)}')
    return jsonify({'ok': True, 'request_count': request_count, 'history_size': len(history_top)})


@app.route('/process', methods=['POST'])
def process_data():


    global request_count
    global history_top
    global last_result
    global reflection_memory
    global search_space
    request_count += 1
    filename = os.path.join(RECORD_DIR, f'turn_{request_count}')
    file = open(filename, 'w')
    file.close()

    payload = request.get_json() or []
    if isinstance(payload, dict):
        data = payload.get('trials') or []
        decision = payload.get('decision') or {}
    else:
        data = payload
        decision = {}
    for item_index, item in enumerate(data):
        last_knobs = item.get('knob')
        throughput = item.get('throughput')
        now_inner_metrics = item.get('metric')

        last_knobs = json.dumps(last_knobs, indent=4)
        now_inner_metrics = json.dumps(now_inner_metrics, indent=4)

        print(last_knobs)
        print(now_inner_metrics)
        print(throughput)

        trial_valid = bool(item.get('valid', throughput > 0))
        if trial_valid and len(history_top) < HISTORY_NUM:
            # If the queue is not full, join directly
            heapq.heappush(
                history_top,
                (throughput, request_count * 100000 + item_index, item),
            )
        elif trial_valid:
            # update the queue
            heapq.heappushpop(
                history_top,
                (throughput, request_count * 100000 + item_index, item),
            )

        if throughput == 0 :
            performance_desc = "0, because database starting failed under current configuration"
        else:
            performance_desc = str(throughput)

        if REFLECTION_ENABLED:
            reflection_memory.update_from_trial(
                knobs=item.get('knob') or {},
                metrics=item.get('metric') or {},
                score=throughput,
                success=trial_valid,
                trial_id=item.get('trial_id') or f'{request_count}:{reflection_memory.turn + 1}',
                notes=item.get('failure_reason'),
            )
        
        sorted_history = sorted(history_top, key=lambda x: -x[0])  
        # Sort by performance
        history_entries = []
        for idx, (t, _, item) in enumerate(sorted_history, 1):
            knob_str = json.dumps(item['knob'], indent=4)
            metric_str = json.dumps(item['metric'], indent=4)
            history_entries.append(
                f"Task {idx}:\n"
                f"Throughput: {t}\n"
                f"Knob Configuration:\n{knob_str}\n"
                f"Metrics:\n{metric_str}\n"
            )
        
        if REFLECTION_ENABLED:
            history_context = reflection_memory.compact_prompt_context(
                max_chars=REFLECTION_MAX_CHARS
            )
        else:
            history_context = "\n\n".join(history_entries)

        # Apply decision to search space (P1.3) - only on first item of the batch
        if item_index == 0:
            best_knob = payload.get('best_knob') or item.get('knob')
            decision_name = decision.get('name', 'continue_local')
            decision_params = decision.get('params', {})

            if decision_name == 'shrink_ranges':
                ratio = decision_params.get('ratio', 0.5)
                report = search_space.shrink(center=best_knob, ratio=ratio)
                print(f'search_space shrink: changed={report["changed"]}, skipped={report["skipped"]}')
            elif decision_name == 'expand_ranges':
                ratio = decision_params.get('ratio', 0.25)
                report = search_space.expand(ratio=ratio)
                print(f'search_space expand: changed={report["changed"]}')
            elif decision_name == 'rollback_safe':
                search_space.reset()
                print(f'search_space reset (rollback_safe)')

        # Use dynamic search space for compact knob representation (P1.3)
        knob_context = knobs if request_count == 1 else search_space.compact_json()
        decision_instruction = (
            f"Adaptive controller action: {decision.get('name', 'continue_local')}. "
            f"Reason: {decision.get('reason', 'none')}."
        )

        messages1 = [
        {
            "role": "system",
            "content": """
                You are an experienced database administrators, skilled in database knob tuning.
            """
        },
        {
            "role": "user",
            "content": """
                Task Overview: 
                Recommend optimal knob configuration based on the inner metrics and workload characteristics in order to optimize the {db_metric} metric.
                knobs:{knob}
                Workload and Database information: 
                - Workload Features: {workload_features}
                - Database Kernel: {database_kernel}
                - Database Scale: {database_scale}
                - Hardware: {hardware}
                Historical Knob Tuning Tasks:
                {history}
                Current Configuration:
                {last_knob}
                Database Feedback:
                - Performance : {performance} 
                - Inner Metrics: {now_inner_metric}
                Output Format:
                Strictly utilize the aforementioned knobs, ensuring that the generated configuration are formatted as follows:
                {{
                    "knob": value, 
                    ……
                }}
                Now, let's think step by step.

                Adaptive Search Direction:
                {decision_instruction}
            """.format(knob=knob_context, inner_metric=inner_metrics, last_knob = last_knobs, now_inner_metric = now_inner_metrics, performance = performance_desc, db_metric = db_metric,history=history_context, workload_features=workload_features if request_count == 1 else "See compact reflection memory and the fixed workload context from the first turn.", database_kernel=database_kernel, hardware=hardware,database_scale=database_scale, decision_instruction=decision_instruction)
        }
        ]

        model = config['configuration recommender']['model']

        for node_index in range(NODE_COUNT):
            usage = call_open_source_llm(model, messages1, filename)
            reflection_memory.record_token_usage(
                usage.get('prompt_tokens', 0),
                usage.get('completion_tokens', 0),
                total_tokens=usage.get('total_tokens', 0),
            )
            with open(TOKEN_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'request_count': request_count,
                    'node_index': node_index,
                    **usage,
                    'cumulative_total_tokens': reflection_memory.token_usage['total'],
                }) + '\n')
    
    with open(filename, 'r') as f:
        data_str = f.read().strip()
    if not data_str:
        print("File is empty")
        data_str = last_result.strip()

    json_strings = [s for s in data_str.split('\n') if s.strip()]
    if json_strings:
        last_result = data_str
    
    top_k, surrogate_diagnostics = _surrogate_select(json_strings, request_count)
    with open(os.path.join(RECORD_DIR, 'top_k'), 'a') as f:
        json.dump(top_k, f, indent=4)
        f.close()

    # send the result to DB_client (include server state for checkpoint)
    return jsonify({
        'recommendations': top_k,
        'request_count': request_count,
        'history_top': list(history_top),
        'last_result': last_result,
        'reflection_state': reflection_memory.to_dict(),
        'surrogate_diagnostics': surrogate_diagnostics,
        'search_space': search_space.to_dict(),  # P1.3: include search space state
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=LLM_SERVER_PORT)