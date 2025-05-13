
import time
import uuid
from datetime import datetime
from flask import current_app
from app.models import Event
from app.services.prompt_service import PromptService
import yaml
import os
import requests
from app.services.llm_service import parse_yaml_response
from dotenv import load_dotenv

import logging
logger = logging.getLogger(__name__)


# 加载环境变量
load_dotenv()

# 大模型配置
LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')
LLM_API_KEY = os.getenv('LLM_API_KEY')
LLM_MODEL = os.getenv('LLM_MODEL', 'gpt-4o-mini')
LLM_MODEL_LONG_TEXT = os.getenv('LLM_MODEL_LONG_TEXT', 'qwen-long')
LLM_TEMPERATURE = float(os.getenv('LLM_TEMPERATURE', 0.6))



def main(event):
    """处理单个安全事件
    
    Args:
        event: Event对象
    """
    start_time = time.time()  # 记录开始时间
    logger.info(f"处理事件: {event.event_id} - {event.event_name}")
    new_round = False if event.status == 'processing' else True
    round_id = event.current_round

    request_data = {
        'type': 'generate_tasks_by_event',
        'req_id': str(uuid.uuid4()),
        'res_id': str(uuid.uuid4()),
        'event_id': event.event_id,
        'round_id': round_id,
        'event_name': event.event_name if event.event_name else '{ 请大模型根据message和context生成 }',
        'message': event.message,
        'context': event.context if event.context else '无',
        'source': event.source if event.source else '无',
        'severity': event.severity if event.severity else '无',
        'created_at': event.created_at.strftime('%Y-%m-%d %H:%M:%S')
    }
    
    tasks = []

    if tasks:
        request_data['history_tasks'] = tasks
    
    yaml_data = yaml.dump(request_data, allow_unicode=True, default_flow_style=False, indent=2)
    logger.info(yaml_data)

    # 针对进入下一轮的事件，提供上一轮的总结信息
    last_round_summary_content = ""
    # 构建用户提示词
    user_prompt = f"""```yaml
{yaml_data}
```
{last_round_summary_content}
针对当前网络安全事件进行分析决策，并分配适当的任务给安全管理员_manager（_analyst, _operator, _coordinator），如果有必要。
"""
    logger.info(user_prompt)
    logger.info("--------------------------------")
    # 调用大模型
    prompt_service = PromptService('_captain')
    system_prompt = prompt_service.get_system_prompt()
    response = call_llm(system_prompt, user_prompt)
    
    logger.info(response)
    logger.info("--------------------------------")
    
    # 解析响应
    parsed_response = parse_yaml_response(response)
    if not parsed_response:
        logger.error(f"解析响应失败: {response}")
        return
    
    # 处理响应
    response_type = parsed_response.get('response_type')
    
    # 如果是任务分配，创建任务
    if response_type == 'TASK':
        print(parsed_response)
    elif response_type == 'MISSION_COMPLETE':
        event.status = 'completed'
    elif response_type == 'ROGER':
        event.status = 'error_from_llm'
        logger.error(f"调用大模型处理事件{event.event_id}失败，原因: {parsed_response.get('response_text', '未知错误')}")
    end_time = time.time()  # 记录结束时间
    elapsed_time = end_time - start_time  # 计算耗时
    print(f"处理事件 {event.event_id} 耗时: {elapsed_time:.3f} 秒")
    logger.info(f"处理事件 {event.event_id} 耗时: {elapsed_time:.3f} 秒")


def call_llm(system_prompt, user_prompt, history=None, temperature=None, long_text=False):
    """调用大模型API
    
    Args:
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        history: 历史对话记录，格式为[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        temperature: 温度参数，控制随机性
        
    Returns:
        大模型返回的文本
    """
    if not LLM_API_KEY:
        raise ValueError("LLM_API_KEY环境变量未设置")
    model = LLM_MODEL_LONG_TEXT if long_text else LLM_MODEL
    
    # 构建消息列表
    messages = [{"role": "system", "content": system_prompt}]
    
    # 添加历史对话
    if history:
        messages.extend(history)
    
    # 添加当前用户提示
    messages.append({"role": "user", "content": user_prompt})
    
    # 设置温度参数
    temp = temperature if temperature is not None else LLM_TEMPERATURE
    
    # 构建请求数据
    data = {
        "model": model,
        "messages": messages,
        "temperature": temp
    }
    
    # 发送请求
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}"
    }
    print(f'{LLM_BASE_URL}/chat/completions , data={data}')
    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers=headers,
        json=data
    )
    
    # 检查响应
    if response.status_code != 200:
        raise Exception(f"API请求失败: {response.status_code} - {response.text}")
    
    # 解析响应
    result = response.json()
    
    response_content = ""
    # 记录请求和响应
    try:
        # 提取响应内容
        response_content = result["choices"][0]["message"]["content"]
        
        # 提取usage信息
        usage = result.get("usage", {})
        print(f"usage={usage}")
        prompt_tokens = usage.get("prompt_tokens", None)
        completion_tokens = usage.get("completion_tokens", None)
        total_tokens = usage.get("total_tokens", None)
        # 提取缓存token信息
        cached_tokens = None
        if usage.get("prompt_tokens_details"):
            cached_tokens = usage["prompt_tokens_details"].get("cached_tokens", None)
        print(f"cached_tokens={cached_tokens}")
    except Exception as e:
        print(f"记录LLM请求失败: {e}")
        # 记录失败不影响主流程，继续返回结果
    
    return response_content


if __name__ == '__main__':
    event = Event(
                event_id=str(uuid.uuid4()),
                event_name = "未授权的访问",
                message = "url https://oa.bank.cn/api/getUserinfo具备未授权访问漏洞，其url的Query参数中可以通过id指定用户信息，而不是通过会话获取出当前用户信息，目前该url是多个业务依赖的通用接口因此不能停止服务，但是漏洞的影响也很大，需要立即处置。",
                context = "",
                source = "12.22.32.42",
                severity = "medium",
                status = 'pending',
                current_round = 1, # 当前处理轮次，默认为1,
                created_at = datetime.now(),
                updated_at = datetime.now()
            )
    main(event)
