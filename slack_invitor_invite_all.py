import json
import boto3
import os
import re
import requests
import time

# 환경 변수에서 Slack 토큰 가져오기
SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']

def lambda_handler(event, context):
    """
    채널 ID를 받아 해당 채널의 네이밍 컨벤션을 확인하고,
    워크스페이스 전체 멤버 중 컨벤션과 일치하는 사용자를 모두 초대하는 함수
    """
    try:
        # 이벤트에서 채널 ID 추출
        channel_id = event.get('channel_id')
        
        if not channel_id:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': '채널 ID가 제공되지 않았습니다.'
                })
            }
        
        print(f"Processing channel: {channel_id}")
        
        # DynamoDB에서 채널 컨벤션 조회
        convention = get_channel_convention(channel_id)
        
        if not convention:
            return {
                'statusCode': 404,
                'body': json.dumps({
                    'error': f'채널 {channel_id}에 설정된 네이밍 컨벤션이 없습니다.'
                })
            }
        
        print(f"Found convention: {convention}")
        
        # 워크스페이스 멤버 목록 가져오기
        members = get_workspace_members()
        
        if not members:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': '워크스페이스 멤버 목록을 가져오는데 실패했습니다.'
                })
            }
        
        print(f"Found {len(members)} members in workspace")
        
        # 컨벤션과 일치하는 멤버 필터링 및 초대
        invited_count = invite_matching_members(channel_id, convention, members)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'채널 {channel_id}에 {invited_count}명의 사용자가 초대되었습니다.',
                'invited_count': invited_count
            })
        }
        
    except Exception as e:
        print(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }

def get_channel_convention(channel_id):
    """DynamoDB에서 채널의 네이밍 컨벤션을 조회합니다."""
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table('slack-invitor')
        
        response = table.get_item(
            Key={
                'channel_id': channel_id
            }
        )
        
        if 'Item' in response:
            return response['Item'].get('name_convention')
        else:
            return None
            
    except Exception as e:
        print(f"Error getting channel convention: {str(e)}")
        raise

def get_workspace_members():
    """Slack API를 사용하여 워크스페이스의 모든 멤버 목록을 가져옵니다."""
    try:
        members = []
        cursor = None
        
        while True:
            url = "https://slack.com/api/users.list"
            headers = {
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json"
            }
            
            params = {}
            if cursor:
                params['cursor'] = cursor
            
            response = requests.get(url, headers=headers, params=params)
            result = response.json()
            
            if not result.get('ok'):
                print(f"Error fetching members: {result.get('error')}")
                return None
            
            members.extend(result.get('members', []))
            
            # 페이지네이션 처리
            cursor = result.get('response_metadata', {}).get('next_cursor')
            if not cursor:
                break
                
            # Rate limit 방지를 위한 지연
            time.sleep(1)
        
        # 봇 및 삭제된 사용자 필터링
        active_members = [
            member for member in members 
            if not member.get('is_bot', False) 
            and not member.get('deleted', False)
            and member.get('id') != 'USLACKBOT'  # Slackbot 제외
        ]
        
        return active_members
        
    except Exception as e:
        print(f"Error getting workspace members: {str(e)}")
        raise

def invite_matching_members(channel_id, convention, members):
    """컨벤션과 일치하는 멤버를 채널에 초대합니다."""
    try:
        invited_count = 0
        
        # 와일드카드를 정규식으로 변환
        if '*' in convention:
            pattern = convention.replace('*', '.*')
            regex = re.compile(f"^{pattern}$")
        else:
            regex = None
        
        # 채널에 이미 있는 멤버 목록 가져오기
        channel_members = get_channel_members(channel_id)
        
        for member in members:
            user_id = member.get('id')
            
            # 이미 채널에 있는 멤버는 건너뛰기
            if user_id in channel_members:
                continue
                
            display_name = member.get('profile', {}).get('display_name', '')
            real_name = member.get('profile', {}).get('real_name', '')
            
            # 사용자 이름 확인 (display_name이 비어있으면 real_name 사용)
            user_name = display_name if display_name else real_name
            
            # 컨벤션과 일치하는지 확인
            is_match = False
            if regex:
                # 와일드카드 패턴 사용
                is_match = bool(regex.match(user_name))
            else:
                # 정확히 일치하는지 확인
                is_match = (user_name == convention)
            
            if is_match:
                # 사용자를 채널에 초대
                invite_result = invite_user_to_channel(user_id, channel_id)
                if invite_result:
                    invited_count += 1
                    print(f"Invited user {user_name} (ID: {user_id}) to channel {channel_id}")
                
                # Rate limit 방지를 위한 지연
                time.sleep(0.5)
        
        return invited_count
        
    except Exception as e:
        print(f"Error inviting matching members: {str(e)}")
        raise

def get_channel_members(channel_id):
    """채널에 이미 있는 멤버 목록을 가져옵니다."""
    try:
        members = set()
        cursor = None
        
        while True:
            url = "https://slack.com/api/conversations.members"
            headers = {
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}"
            }
            
            params = {
                "channel": channel_id
            }
            
            if cursor:
                params['cursor'] = cursor
            
            response = requests.get(url, headers=headers, params=params)
            result = response.json()
            
            if not result.get('ok'):
                error = result.get('error')
                print(f"Error fetching channel members: {error}")
                
                # 채널을 찾을 수 없는 경우 빈 세트 반환
                if error == 'channel_not_found':
                    return set()
                    
                raise Exception(f"Failed to get channel members: {error}")
            
            members.update(result.get('members', []))
            
            # 페이지네이션 처리
            cursor = result.get('response_metadata', {}).get('next_cursor')
            if not cursor:
                break
                
            # Rate limit 방지를 위한 지연
            time.sleep(0.5)
        
        return members
        
    except Exception as e:
        print(f"Error getting channel members: {str(e)}")
        raise

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
            return True
        else:
            error = result.get('error', 'unknown_error')
            # 이미 채널에 있는 경우는 성공으로 처리
            if error == 'already_in_channel':
                return True
            else:
                print(f"Failed to invite user {user_id} to channel {channel_id}: {error}")
                return False
    
    except Exception as e:
        print(f"Error inviting user to channel: {str(e)}")
        return False
