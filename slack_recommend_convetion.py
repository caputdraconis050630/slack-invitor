import json
import os
import boto3
import requests
from datetime import datetime

# AWS 클라이언트
dynamodb = boto3.resource('dynamodb')
bedrock_runtime = boto3.client(
    service_name='bedrock-runtime',
    region_name='us-east-1'
)

# 환경 변수
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'slack-invitor')
MODEL_ID = 'amazon.nova-micro-v1:0'  # Nova Micro 모델 ID

def lambda_handler(event, context):
    """
    /recommend-convention 슬랙 명령어를 처리하는 Lambda 핸들러 함수
    """
    body = event.get('body', '')
    if isinstance(body, str):
        try:
            if 'isBase64Encoded' in event and event['isBase64Encoded']:
                import base64
                body = base64.b64decode(body).decode('utf-8')
            
            params = {}
            for item in body.split('&'):
                if '=' in item:
                    key, value = item.split('=', 1)
                    params[key] = value
        except Exception as e:
            print(f"요청 본문 파싱 오류: {str(e)}")
            return {
                'statusCode': 400,
                'body': json.dumps({'error': '요청 형식이 잘못되었습니다'})
            }
    else:
        params = {}
    
    # 채널 ID 가져오기
    channel_id = params.get('channel_id', '')
    if not channel_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': '채널 ID가 제공되지 않았습니다'})
        }
    
    # 슬랙 API에서 채널 정보 가져오기
    channel_info = get_channel_info(channel_id)
    if not channel_info or 'error' in channel_info:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': f"채널 정보를 가져오는데 실패했습니다: {channel_info.get('error', '알 수 없는 오류')}"})
        }
    
    # 채널 이름 가져오기
    channel_name = channel_info.get('channel', {}).get('name', '')
    if not channel_name:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': '채널 이름을 가져오는데 실패했습니다'})
        }
    
    # DynamoDB에서 모든 기존 컨벤션 가져오기
    existing_conventions = get_existing_conventions()
    
    # Amazon Bedrock을 사용하여 네이밍 컨벤션 추천 생성
    recommended_convention = generate_convention_recommendation(channel_name, existing_conventions)
    
    # 슬랙에 응답 보내기
    response_text = f"이 채널에 추천하는 네이밍 컨벤션: `{recommended_convention}`\n\n이 컨벤션을 설정하려면: `/set-convention {recommended_convention}`"
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'response_type': 'in_channel',  # 채널에 공개적으로 표시
            'text': response_text
        }),
        'headers': {
            'Content-Type': 'application/json'
        }
    }

def get_channel_info(channel_id):
    """
    슬랙 API에서 채널 정보 가져오기
    """
    url = 'https://slack.com/api/conversations.info'
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    params = {
        'channel': channel_id
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response_data = response.json()
        
        if response.status_code == 200 and response_data.get('ok', False):
            return response_data
        else:
            return {'error': response_data.get('error', '알 수 없는 오류')}
    except Exception as e:
        return {'error': str(e)}

def get_existing_conventions():
    """
    DynamoDB에서 모든 기존 네이밍 컨벤션 가져오기
    """
    table = dynamodb.Table(DYNAMODB_TABLE)
    
    try:
        # 테이블 스캔하여 모든 항목 가져오기
        response = table.scan()
        items = response.get('Items', [])
        
        # 더 많은 항목이 있는 경우 계속 스캔 (페이지네이션)
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
        
        # 컨벤션과 채널 ID 추출
        conventions = []
        for item in items:
            if 'name_convention' in item and item['name_convention']:
                conventions.append({
                    'channel_id': item.get('channel_id', ''),
                    'convention': item.get('name_convention', '')
                })
        
        return conventions
    except Exception as e:
        print(f"DynamoDB에서 컨벤션 가져오기 오류: {str(e)}")
        return []

def generate_convention_recommendation(channel_name, existing_conventions):
    """
    Amazon Bedrock을 사용하여 네이밍 컨벤션 추천 생성
    """
    # Bedrock을 위한 프롬프트 생성
    prompt_text = create_bedrock_prompt(channel_name, existing_conventions)
    
    try:
        # Nova 모델 요청 형식 (Claude와 다름)
        request_body = {
            "inputText": prompt_text,
            "textGenerationConfig": {
                "maxTokenCount": 100,
                "temperature": 0.5,
                "topP": 0.5,
                "stopSequences": []
            }
        }
        
        # API 호출
        response = bedrock_runtime.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(request_body)
        )
        
        # 응답 파싱 (Nova 응답 형식에 맞춤)
        response_body = json.loads(response['body'].read())
        generated_text = response_body.get('results', [{}])[0].get('outputText', '').strip()
        
        return generated_text
    except Exception as e:
        print(f"Bedrock 호출 오류: {str(e)}")
        # 실패 시 기본 추천
        return f"{channel_name.lower()}*"

def create_bedrock_prompt(channel_name, existing_conventions):
    """
    Amazon Bedrock에 전달할 프롬프트 생성
    """
    conventions_list = []
    for conv in existing_conventions:
        channel_id = conv.get('channel_id', '')
        convention = conv.get('convention', '')
        conventions_list.append(f"- 채널 ID: {channel_id}, 컨벤션: {convention}")
    
    conventions_text = "\n".join(conventions_list) if conventions_list else "기존 컨벤션이 없습니다."
    
    prompt = f"""
    슬랙 채널의 네이밍 컨벤션을 추천해주세요. 이 네이밍 컨벤션은 패턴과 일치하는 사용자 이름을 가진 사용자를 자동으로 초대하는 데 사용됩니다.

    현재 채널 이름: {channel_name}
    
    슬랙 워크스페이스의 기존 네이밍 컨벤션:
    {conventions_text}
    
    네이밍 컨벤션 요구사항:
    1. 채널 이름 "{channel_name}"과 관련이 있어야 함
    2. 잘못된 사용자 초대를 방지하기 위해 기존의 다른 채널에서 사용중인 컨벤션과 충돌하지 않아야 함. 현재 채널에 추천받은 컨벤션으로 설정 시에, 의도치 않은 초대가 이루어지지 않아야 함.
    3. 여러 유사한 이름을 매칭하기 위해 와일드카드(*)를 사용할 수 있음
    4. 간단하고 명확해야 함
    5. 기존의 컨벤션들과 비슷한 형식을 유지해야 함(e.g. 2025_인하대_캡스톤 채널의 사용자들은 2025_인하대_캡스톤_*  와일드카드에는 성명 등이 들어감)
    
    중요: 응답에는 추천하는 네이밍 컨벤션 텍스트만 포함해야 합니다.
    설명, 소개 또는 다른 텍스트를 포함하지 마세요.
    추천 컨벤션 패턴만 출력하세요.
    """
    
    return prompt
