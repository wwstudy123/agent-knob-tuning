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
compact_knobs = json.dumps(
    {
        key: {
            field: value
            for field, value in meta.items()
            if field in ('min_value', 'max_value', 'step', 'type', 'enum_values', 'special_value')
        }
        for key, meta in knob_metadata.items()
    },
    separators=(',', ':'),
)

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
    # match "key": value 
    pattern = re.compile(r'"(\w+)":\s*([\d.]+)')
    matches = pattern.findall(json_string)
    data = {key: int(value) for key, value in matches}
    return data

def convert_to_bytes(value):
    units = {
        'B': 1,
        'KB': 1024,
        'MB': 1024**2,
        'GB': 1024**3,
        'TB': 1024**4
    }
    match = re.match(r'(\d+)([KMGT]B)', value)
    if match:
        number = int(match.group(1))
        unit = match.group(2)
        return number * units[unit]
    return int(value)

def replace_units(json_string):
    def replace_match(match):
        return str(convert_to_bytes(match.group(0)))
    
    json_string = re.sub(r'\d+[KMGT]B', replace_match, json_string)
    return json_string


def remove_comments(json_string):
    json_string = re.sub(r'//.*', '', json_string)
    json_string = re.sub(r'/\*.*?\*/', '', json_string, flags=re.DOTALL)
    json_string = re.sub(r',\s*}', '}', json_string)
    json_string = re.sub(r',\s*]', ']', json_string)
    return json_string

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

    pattern = r'\{[^{}]+\}'
    match = re.search(pattern, content_text, re.DOTALL)

    if match:
        json_str = match.group(0)
        json_str = replace_units(json_str)
        config_dict = extract_key_value_pairs(json_str)
        #json_str = remove_comments(json_str)
        #config_dict = json.loads(json_str)
        print(config_dict)
        if not config_dict:
            return usage
        config_dict = json.dumps(config_dict)
        with open(filename, 'r') as f:
            data_str = f.read()

        # Split the data into individual JSON strings
        json_strings = data_str.strip().split('\n')

        # # Prepare the final structured JSON format
        # for json_str in json_strings:
        #     d = json.loads(json_str)
        if config_dict in json_strings :
            return usage
        with open(filename, 'a') as f:
            print("sucess recommendation!")
            f.write('\n')
            f.write(config_dict)

    else:
        print("No JSON configuration found in the input.")
    return usage

history_top = []
last_result = ""
app = Flask(__name__)
request_count = 0


@app.route('/restore', methods=['POST'])
def restore_state():
    """Restore LLM_server in-memory state from DB_client checkpoint."""
    global request_count, history_top, last_result, reflection_memory
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
    print(f'LLM_server restored: request_count={request_count}, history_top={len(history_top)}')
    return jsonify({'ok': True, 'request_count': request_count, 'history_size': len(history_top)})


@app.route('/process', methods=['POST'])
def process_data():


    global request_count
    global history_top
    global last_result
    global reflection_memory
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
        knob_context = knobs if request_count == 1 else compact_knobs
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
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=LLM_SERVER_PORT)