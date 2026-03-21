import os
import serverless_wsgi  # You'll need to add this to requirements.txt
from server.app import app

def handler(event, context):
    return serverless_wsgi.handle_request(app, event, context)

# Keep this for local testing only
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)