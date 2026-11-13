import os
import uvicorn

if __name__ == '__main__':
    uvicorn.run('api_gateway.api:create_app',factory=True,host=os.environ.get('GATEWAY_HOST','127.0.0.1'),
                port=int(os.environ.get('GATEWAY_PORT','8000')), access_log=False)
