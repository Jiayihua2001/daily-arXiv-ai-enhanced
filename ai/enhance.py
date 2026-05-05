import os
import json
import sys
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict
from queue import Queue
from threading import Lock
# INSERT_YOUR_CODE
import requests

import dotenv
import argparse
from tqdm import tqdm

import langchain_core.exceptions
from langchain_openai import ChatOpenAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from structure import Structure
from openai import OpenAI
from llm_compat import chat_create as _chat_create

if os.path.exists('.env'):
    dotenv.load_dotenv()
template = open("template.txt", "r").read()
system = open("system.txt", "r").read()

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, required=True, help="jsonline data file")
    parser.add_argument("--max_workers", type=int, default=1, help="Maximum number of parallel workers")
    return parser.parse_args()

SCHEMA_FIELDS = ["tldr", "motivation", "method", "result", "conclusion"]


def _llm_json(client: OpenAI, model_name: str, system_prompt: str, user_prompt: str,
              language: str, debug_label: str = "") -> Dict:
    """
    Call the OpenAI-compatible chat API with response_format=json_object,
    asking explicitly for a JSON object with the SCHEMA_FIELDS keys. Robust
    to providers that don't honor json_object and just return text — we
    extract any JSON object found in the response.

    Raises on hard failures (network, auth, etc.). Returns {} if the model
    answered but produced un-parseable output.
    """
    sys_with_json = (
        system_prompt.format(language=language).rstrip()
        + "\n\nRespond with a single valid JSON object containing exactly "
          f"these keys: {SCHEMA_FIELDS}. "
          "Do not include any text before or after the JSON object."
    )

    # Try with response_format first, fall back without it (some
    # providers reject the param even though OpenAI accepts it).
    last_text = ""
    for attempt, kwargs_extra in enumerate([
        {"response_format": {"type": "json_object"}},
        {},
    ]):
        try:
            resp = _chat_create(
                client,
                model=model_name,
                messages=[
                    {"role": "system",  "content": sys_with_json},
                    {"role": "user",    "content": user_prompt},
                ],
                temperature=0.3,
                **kwargs_extra,
            )
            text = resp.choices[0].message.content or ""
            last_text = text
            data = _extract_json_obj(text)
            if data:
                if debug_label and not getattr(_llm_json, "_logged_first_ok", False):
                    print(f"[enhance] ✓ first OK ({debug_label}): "
                          f"keys={sorted(data.keys())}",
                          file=sys.stderr)
                    _llm_json._logged_first_ok = True
                return data
            print(f"[enhance] {debug_label} attempt {attempt+1}: returned non-JSON "
                  f"text (first 200 chars): {text[:200]!r}", file=sys.stderr)
        except Exception as e:
            # Surface the FIRST error verbosely — we've been blind to this.
            if not getattr(_llm_json, "_logged_first_err", False):
                import traceback
                print(f"\n{'='*60}\n[enhance] FIRST CALL FAILED — full traceback:"
                      f"\n{'='*60}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                print(f"{'='*60}\n[enhance] item: {debug_label}\n"
                      f"[enhance] OPENAI_BASE_URL = {os.environ.get('OPENAI_BASE_URL','(unset)')}\n"
                      f"[enhance] MODEL_NAME       = {model_name}\n"
                      f"{'='*60}", file=sys.stderr)
                _llm_json._logged_first_err = True
            # If response_format was the culprit, the next iteration drops it.
            print(f"[enhance] {debug_label} attempt {attempt+1}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    return {}


def _extract_json_obj(text: str) -> Dict:
    """Find and parse the first {...} JSON object in `text`."""
    if not text:
        return {}
    # Direct parse first.
    try:
        v = json.loads(text)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    # Strip markdown code fences (some models wrap JSON in ```json ... ```).
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            v = json.loads(fenced.group(1))
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    # Find the first balanced {…} object.
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start != -1:
                blob = text[start:i+1]
                try:
                    v = json.loads(blob)
                    if isinstance(v, dict):
                        return v
                except Exception:
                    # Try once more after escaping bare backslashes (LaTeX).
                    try:
                        v = json.loads(blob.replace("\\", "\\\\"))
                        if isinstance(v, dict):
                            return v
                    except Exception:
                        pass
                start = -1
    return {}


def process_single_item(chain, item: Dict, language: str) -> Dict:
    # The spam-check service is an UPSTREAM Cloudflare worker, not ours.
    # When it 429s, times out, or goes down, the original code returned
    # True (= "sensitive") on every error, which silently dropped EVERY
    # paper. With ~6 checks per paper × 500 papers/day that's 3000+ calls
    # to a third-party service every run — the dominant failure mode.
    #
    # Default is now OFF for personal forks. Set ENABLE_SPAM_CHECK=true
    # in the workflow if you actually need content moderation. When ON,
    # service errors now FAIL OPEN (return False = "not sensitive") so a
    # bad spam-service day doesn't wipe the whole feed.
    SPAM_CHECK_ENABLED = os.environ.get("ENABLE_SPAM_CHECK", "").lower() in ("1", "true", "yes")

    def is_sensitive(content: str) -> bool:
        """Check via spam.dw-dengwei.workers.dev. Returns True if sensitive."""
        if not SPAM_CHECK_ENABLED:
            return False
        try:
            resp = requests.post(
                "https://spam.dw-dengwei.workers.dev",
                json={"text": content},
                timeout=5
            )
            if resp.status_code == 200:
                result = resp.json()
                return bool(result.get("sensitive", False))
            print(f"[enhance] spam-check status={resp.status_code}; failing open",
                  file=sys.stderr)
            return False  # fail open: don't drop on service hiccup
        except Exception as e:
            print(f"[enhance] spam-check error: {e}; failing open", file=sys.stderr)
            return False  # fail open

    def check_github_code(content: str) -> Dict:
        """提取并验证 GitHub 链接"""
        code_info = {}

        # 1. 优先匹配 github.com/owner/repo 格式
        github_pattern = r"https?://github\.com/([a-zA-Z0-9-_]+)/([a-zA-Z0-9-_\.]+)"
        match = re.search(github_pattern, content)
        
        if match:
            owner, repo = match.groups()
            # 清理 repo 名称，去掉可能的 .git 后缀或末尾的标点
            repo = repo.rstrip(".git").rstrip(".,)")
            
            full_url = f"https://github.com/{owner}/{repo}"
            code_info["code_url"] = full_url
            
            # 尝试调用 GitHub API 获取信息
            github_token = os.environ.get("TOKEN_GITHUB")
            headers = {"Accept": "application/vnd.github.v3+json"}
            if github_token:
                headers["Authorization"] = f"token {github_token}"
            
            try:
                api_url = f"https://api.github.com/repos/{owner}/{repo}"
                resp = requests.get(api_url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    code_info["code_stars"] = data.get("stargazers_count", 0)
                    code_info["code_last_update"] = data.get("pushed_at", "")[:10]
            except Exception:
                # API 调用失败不影响主流程
                pass
            return code_info

        # 2. 如果没有 github.com，尝试匹配 github.io
        github_io_pattern = r"https?://[a-zA-Z0-9-_]+\.github\.io(?:/[a-zA-Z0-9-_\.]+)*"
        match_io = re.search(github_io_pattern, content)
        
        if match_io:
            url = match_io.group(0)
            # 清理末尾标点
            url = url.rstrip(".,)")
            code_info["code_url"] = url
            # github.io 不进行 star 和 update 判断
                
        return code_info

    # 检查 summary 字段
    if is_sensitive(item.get("summary", "")):
        return None

    # 检测代码可用性
    code_info = check_github_code(item.get("summary", ""))
    if code_info:
        item.update(code_info)

    """处理单个数据项"""
    # Default structure with meaningful fallback values
    default_ai_fields = {
        "tldr": "Summary generation failed",
        "motivation": "Motivation analysis unavailable",
        "method": "Method extraction failed",
        "result": "Result analysis unavailable",
        "conclusion": "Conclusion extraction failed"
    }
    
    # Pass-in 'chain' is now (client, model_name, system_prompt, user_template).
    client, model_name, system_prompt, user_template = chain
    user_prompt = user_template.format(content=item['summary'])
    parsed = _llm_json(
        client, model_name, system_prompt, user_prompt, language,
        debug_label=item.get("id", "?"),
    )
    if parsed:
        # Coerce all values to strings (LLMs sometimes nest objects).
        item['AI'] = {
            k: (parsed[k] if isinstance(parsed.get(k), str)
                else json.dumps(parsed[k], ensure_ascii=False) if k in parsed
                else default_ai_fields[k])
            for k in default_ai_fields.keys()
        }
    else:
        item['AI'] = default_ai_fields
    
    # Final validation to ensure all required fields exist
    for field in default_ai_fields.keys():
        if field not in item['AI']:
            item['AI'][field] = default_ai_fields[field]

    # 检查 AI 生成的所有字段
    for v in item.get("AI", {}).values():
        if is_sensitive(str(v)):
            return None
    return item

def process_all_items(data: List[Dict], model_name: str, language: str, max_workers: int) -> List[Dict]:
    """并行处理所有数据项"""
    # Bypass langchain's structured-output entirely — it's been a major
    # source of opaque failures (DeepSeek's json_mode requires the literal
    # word 'JSON' in the prompt; function_calling has version drift). We
    # use the raw openai client which DeepSeek's API is fully compatible
    # with, and parse JSON ourselves.
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    api_key  = os.environ.get("OPENAI_API_KEY")
    print(f"[enhance] Connect to: {model_name} via "
          f"{base_url or '(default openai.com)'}", file=sys.stderr)

    client_kwargs = {}
    if base_url: client_kwargs["base_url"] = base_url
    if api_key:  client_kwargs["api_key"]  = api_key
    client = OpenAI(**client_kwargs)

    # 'chain' is now a tuple consumed by process_single_item.
    chain = (client, model_name, system, template)

    # 使用线程池并行处理
    processed_data = [None] * len(data)  # 预分配结果列表
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_idx = {
            executor.submit(process_single_item, chain, item, language): idx
            for idx, item in enumerate(data)
        }
        
        # 使用tqdm显示进度
        for future in tqdm(
            as_completed(future_to_idx),
            total=len(data),
            desc="Processing items"
        ):
            idx = future_to_idx[future]
            try:
                result = future.result()
                processed_data[idx] = result
            except Exception as e:
                print(f"Item at index {idx} generated an exception: {e}", file=sys.stderr)
                # Add default AI fields to ensure consistency
                processed_data[idx] = data[idx]
                processed_data[idx]['AI'] = {
                    "tldr": "Processing failed",
                    "motivation": "Processing failed",
                    "method": "Processing failed",
                    "result": "Processing failed",
                    "conclusion": "Processing failed"
                }
    
    return processed_data

def main():
    args = parse_args()
    model_name = os.environ.get("MODEL_NAME", 'deepseek-chat')
    language = os.environ.get("LANGUAGE", 'Chinese')

    # 检查并删除目标文件
    target_file = args.data.replace('.jsonl', f'_AI_enhanced_{language}.jsonl')
    if os.path.exists(target_file):
        os.remove(target_file)
        print(f'Removed existing file: {target_file}', file=sys.stderr)

    # 读取数据
    data = []
    with open(args.data, "r") as f:
        for line in f:
            data.append(json.loads(line))

    # 去重
    seen_ids = set()
    unique_data = []
    for item in data:
        if item['id'] not in seen_ids:
            seen_ids.add(item['id'])
            unique_data.append(item)

    data = unique_data
    print('Open:', args.data, file=sys.stderr)
    
    # 并行处理所有数据
    processed_data = process_all_items(
        data,
        model_name,
        language,
        args.max_workers
    )
    
    # 保存结果
    with open(target_file, "w") as f:
        for item in processed_data:
            if item is not None:
                f.write(json.dumps(item) + "\n")

if __name__ == "__main__":
    main()
