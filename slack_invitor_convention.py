import json
import boto3
import datetime
import base64
from urllib.parse import parse_qs, unquote

def lambda_handler(event, context):
    # 슬랙에서 전송된 요청 파싱
    body = parse_slack_request(event)
    
    # 슬래시 명령어 검증
    if body.get('command') != '/set-convention':
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'response_type': 'ephemeral',
                'text': '지원되지 않는 명령어입니다.'
            })
        }
    
    # 채널 ID와 컨벤션 텍스트 추출
    channel_id = body.get('channel_id')
    name_convention = body.get('text', '').strip()
    
    # DynamoDB 클라이언트 생성
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table('slack-invitor')
    
    # Lambda 클라이언트 생성 (비동기 호출용)
    lambda_client = boto3.client('lambda')
    
    # 현재 날짜 및 시간 생성 (시간, 분, 초 포함)
    current_datetime = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        # 컨벤션 텍스트가 비어있는지 확인 - 비어있으면 컨벤션 삭제
        if not name_convention:
            # 채널 ID로 기존 항목 조회
            response = table.get_item(
                Key={
                    'channel_id': channel_id
                }
            )
            
            # 항목이 존재하는 경우 삭제
            if 'Item' in response:
                table.delete_item(
                    Key={
                        'channel_id': channel_id
                    }
                )
                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'response_type': 'in_channel',
                        'text': '이 채널의 이름 컨벤션이 삭제되었습니다.'
                    })
                }
            else:
                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'response_type': 'ephemeral',
                        'text': '이 채널에 설정된 이름 컨벤션이 없습니다.'
                    })
                }
        
        # 띄어쓰기가 포함된 문자열인지 확인
        if ' ' in name_convention:
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'response_type': 'ephemeral',
                    'text': '이름 컨벤션은 띄어쓰기 없이 단일 문자열로 입력해주세요. 예: `/set-convention test`'
                })
            }
        
        # 채널 ID로 기존 항목 조회
        response = table.get_item(
            Key={
                'channel_id': channel_id
            }
        )
        
        # 항목이 이미 존재하는 경우 업데이트
        if 'Item' in response:
            update_response = table.update_item(
                Key={
                    'channel_id': channel_id
                },
                UpdateExpression='SET name_convention = :nc, updated_date = :ud',
                ExpressionAttributeValues={
                    ':nc': name_convention,
                    ':ud': current_datetime
                },
                ReturnValues='UPDATED_NEW'
            )
            
            # 비동기로 초대 람다 함수 호출
            invoke_invite_lambda(lambda_client, channel_id, name_convention)
            
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'response_type': 'in_channel',
                    'text': f'이 채널의 이름 컨벤션이 `{name_convention}`으로 업데이트되었습니다. 모든 사용자 초대가 백그라운드에서 진행됩니다.'
                })
            }
        # 항목이 존재하지 않는 경우 새로 생성
        else:
            table.put_item(
                Item={
                    'channel_id': channel_id,
                    'name_convention': name_convention,
                    'created_date': current_datetime
                }
            )
            
            # 비동기로 초대 람다 함수 호출
            invoke_invite_lambda(lambda_client, channel_id, name_convention)
            
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'response_type': 'in_channel',
                    'text': f'이 채널의 이름 컨벤션이 `{name_convention}`으로 설정되었습니다. 모든 사용자 초대가 백그라운드에서 진행됩니다.'
                })
            }
            
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'response_type': 'ephemeral',
                'text': f'오류가 발생했습니다: {str(e)}'
            })
        }

def invoke_invite_lambda(lambda_client, channel_id, name_convention):
    """
    비동기적으로 사용자 초대 람다 함수를 호출합니다.
    """
    try:
        # 초대 람다 함수에 전달할 페이로드
        payload = {
            'channel_id': channel_id,
            'name_convention': name_convention
        }
        
        # 비동기 호출 (InvocationType='Event')
        response = lambda_client.invoke(
            FunctionName='slack_invitor_invite_all',  # 초대 람다 함수 이름 (필요에 따라 변경)
            InvocationType='Event',  # 비동기 호출
            Payload=json.dumps(payload)
        )
        
        print(f"Lambda invocation response: {response}")
        return True
    except Exception as e:
        print(f"Error invoking invite lambda: {str(e)}")
        return False

def parse_slack_request(event):
    """
    Slack에서 전송된 요청을 파싱합니다.
    API Gateway를 통해 전달된 요청 본문을 처리합니다.
    Base64로 인코딩된 본문을 디코딩하고 URL 인코딩된 폼 데이터를 파싱합니다.
    """
    try:
        # 이벤트 로깅 (디버깅용)
        print(f"Received event: {json.dumps(event)}")
        
        if 'body' in event:
            body_str = event['body']
            
            # Base64로 인코딩된 경우 디코딩
            if event.get('isBase64Encoded', False):
                body_str = base64.b64decode(body_str).decode('utf-8')
                print(f"Decoded body: {body_str}")
            
            # URL 인코딩된 폼 데이터 파싱
            parsed_body = parse_qs(body_str)
            
            # parse_qs는 모든 값을 리스트로 반환하므로, 단일 값인 경우 첫 번째 항목만 추출
            result = {}
            for key, value in parsed_body.items():
                if isinstance(value, list) and len(value) == 1:
                    result[key] = value[0]
                else:
                    result[key] = value
            
            print(f"Parsed body: {result}")
            return result
        else:
            # 직접 호출된 경우
            return event
    except Exception as e:
        print(f"Error parsing request: {str(e)}")
        return {}
