import json
import boto3
import re
import base64
import requests
from urllib.parse import parse_qs

# 환경 변수에서 Slack 토큰 가져오기
SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']

def lambda_handler(event, context):
    # 이벤트 로깅
    print(f"Received event: {json.dumps(event)}")
    
    # 이벤트 파싱
    body = parse_slack_event(event)
    
    # 이벤트 타입 확인
    event_type = body.get('event', {}).get('type')
    
    # DynamoDB 클라이언트 생성
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table('slack-invitor')
    
    try:
        # 새 사용자 참여 이벤트 처리
        if event_type == 'team_join':
            return handle_team_join(body, table)
        
        # 사용자 프로필 변경 이벤트 처리
        elif event_type == 'user_change':
            return handle_user_change(body, table)
        
        # 지원되지 않는 이벤트 타입
        else:
            print(f"Unsupported event type: {event_type}")
            return {
                'statusCode': 200,
                'body': json.dumps('Event received')
            }
            
    except Exception as e:
        print(f"Error processing event: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

def parse_slack_event(event):
    """Slack 이벤트를 파싱합니다."""
    try:
        if 'body' in event:
            body_str = event['body']
            
            # Base64로 인코딩된 경우 디코딩
            if event.get('isBase64Encoded', False):
                body_str = base64.b64decode(body_str).decode('utf-8')
            
            # JSON 형식인 경우
            try:
                body = json.loads(body_str)
                return body
            except json.JSONDecodeError:
                # URL 인코딩된 폼 데이터인 경우
                parsed_body = parse_qs(body_str)
                result = {}
                for key, value in parsed_body.items():
                    if isinstance(value, list) and len(value) == 1:
                        result[key] = value[0]
                    else:
                        result[key] = value
                return result
        else:
            return event
    except Exception as e:
        print(f"Error parsing event: {str(e)}")
        return {}

def handle_team_join(body, table):
    """새 사용자 참여 이벤트를 처리합니다."""
    user = body.get('event', {}).get('user', {})
    user_id = user.get('id')
    display_name = user.get('profile', {}).get('display_name', '')
    real_name = user.get('profile', {}).get('real_name', '')
    
    # 사용자 이름 확인 (display_name이 비어있으면 real_name 사용)
    user_name = display_name if display_name else real_name
    
    print(f"New user joined: {user_name} (ID: {user_id})")
    
    # 모든 채널 컨벤션 가져오기
    return check_and_invite_user(user_id, user_name, table)

def handle_user_change(body, table):
    """사용자 프로필 변경 이벤트를 처리합니다."""
    user = body.get('event', {}).get('user', {})
    user_id = user.get('id')
    display_name = user.get('profile', {}).get('display_name', '')
    real_name = user.get('profile', {}).get('real_name', '')
    
    # 사용자 이름 확인 (display_name이 비어있으면 real_name 사용)
    user_name = display_name if display_name else real_name
    
    print(f"User profile changed: {user_name} (ID: {user_id})")
    
    # 모든 채널 컨벤션 가져오기
    return check_and_invite_user(user_id, user_name, table)

def check_and_invite_user(user_id, user_name, table):
    """사용자 이름이 컨벤션과 일치하는지 확인하고 채널에 초대합니다."""
    try:
        # 모든 채널 컨벤션 가져오기
        response = table.scan()
        conventions = response.get('Items', [])
        
        invited_channels = []
        
        # 각 컨벤션 확인
        for convention in conventions:
            channel_id = convention.get('channel_id')
            name_convention = convention.get('name_convention', '')
            
            # 와일드카드를 정규식으로 변환
            if '*' in name_convention:
                # *를 정규식 .*로 변환 (임의의 문자열과 매칭)
                pattern = name_convention.replace('*', '.*')
                if re.match(f"^{pattern}$", user_name):
                    # 컨벤션과 일치하면 채널에 초대
                    invite_result = invite_user_to_channel(user_id, channel_id)
                    if invite_result:
                        invited_channels.append(channel_id)
            else:
                # 정확히 일치하는지 확인
                if user_name == name_convention:
                    invite_result = invite_user_to_channel(user_id, channel_id)
                    if invite_result:
                        invited_channels.append(channel_id)
        
        if invited_channels:
            print(f"User {user_name} invited to channels: {', '.join(invited_channels)}")
        else:
            print(f"User {user_name} does not match any conventions")
        
        return {
            'statusCode': 200,
            'body': json.dumps('User processed')
        }
        
    except Exception as e:
        print(f"Error checking conventions: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

def invite_user_to_channel(user_id, channel_id):
    """Slack API를 사용하여 사용자를 채널에 초대합니다."""
    try:
        url = "https://slack.com/api/conversations.invite"
        headers = {
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "channel": channel_id,
            "users": user_id
        }
        
        response = requests.post(url, headers=headers, json=payload)
        result = response.json()
        
        if result.get('ok'):
            print(f"Successfully invited user {user_id} to channel {channel_id}")
            return True
        else:
            error = result.get('error', 'unknown_error')
            # 이미 채널에 있는 경우는 성공으로 처리
            if error == 'already_in_channel':
                print(f"User {user_id} is already in channel {channel_id}")
                return True
            else:
                print(f"Failed to invite user {user_id} to channel {channel_id}: {error}")
                return False
    
    except Exception as e:
        print(f"Error inviting user to channel: {str(e)}")
        return False
