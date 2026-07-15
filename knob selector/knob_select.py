import configparser
import re
import json
import os

# Auto-detect: use Anthropic SDK if env vars are set, otherwise OpenAI SDK
_use_anthropic = bool(os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))
if _use_anthropic:
    import anthropic
else:
    from openai import OpenAI

config = configparser.ConfigParser()
config.read('./config.ini')

file_path = config['workload analyzer']['output_file']
with open(file_path, "r") as f:
    workload_features = f.read().strip()

candidate_knobs = "./knob selector/renamed_knobs"
with open(candidate_knobs, "r") as f:
    knobs = f.read().strip()

knob_num = config['knob selector']['knob_num']
database_kernel = config['knob selector']['database_kernel']
hardware = config['knob selector']['hardware']
database_scale = config['knob selector']['database_scale']
db_metric = config['configuration recommender']['db_metric']

messages = [
    {
        "role": "system",
        "content": (
            "You are an experienced database administrator, skilled in database knob tuning. "
            "You will determine which knobs are worth tuning. "
            "You only tune knobs that have a significant impact on DBMS performance."
        ),
    },
    {
        "role": "user",
        "content": f"""Task Overview:
Select the {knob_num} most important knobs from the provided candidates to optimize the {db_metric} metric.

Candidate Knobs:
{knobs}

Workload and Database information:
- Workload Features: {workload_features}
- Database Kernel: {database_kernel}
- Database Scale: {database_scale}
- Hardware: {hardware}

Output Format:
Only output selected knobs, each on its own line, strictly as:
**knobN**: brief reason

Example:
**knob10**: buffer pool size
**knob4**: max connections

Do not invent knob names that are not in the candidate list.
Now, think step by step, then output the final list.
""",
    },
]


def extract_knob_names(content_text):
    """Extract knobN names from various LLM response formats."""
    if not content_text:
        return []

    patterns = [
        r"\*\*(knob\d+)\*\*",   # **knob10**
        r"<(knob\d+)>",          # <knob10>
        r'"(knob\d+)"',          # "knob10"
        r"`(knob\d+)`",          # `knob10`
        r"\b(knob\d+)\b",        # plain knob10
    ]

    for pattern in patterns:
        names = re.findall(pattern, content_text, flags=re.IGNORECASE)
        if names:
            # Normalize to lowercase knobN and preserve order while de-duping
            seen = set()
            ordered = []
            for name in names:
                key = name.lower()
                if key not in seen:
                    seen.add(key)
                    ordered.append(key)
            return ordered
    return []


def call_open_source_llm_1(model):
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
            temperature=0,
        )
        content_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                content_text = block.text
                break
    else:
        client = OpenAI(
            api_key=config['knob selector']['api_key'],
            base_url=config['knob selector']['base_url'],
        )
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
        )
        if not completion.choices:
            raise ValueError("API returned empty choices")
        content_text = completion.choices[0].message.content or ""

    print(content_text)
    print("--------------------------")

    knob_names = extract_knob_names(content_text)
    if not knob_names:
        print("Warning: no knob names extracted from LLM response")
    return set(knob_names)


if __name__ == '__main__':
    model = config['knob selector']['model']
    knob_set = call_open_source_llm_1(model)
    print(f"Selected {len(knob_set)} knobs: {knob_set}")

    output = config['knob selector']['output_file']
    with open(output, 'w') as f:
        json.dump(list(knob_set), f)
    print(f"Saved to {output}")