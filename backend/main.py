import os
import serverless_wsgi  # You'll need to add this to requirements.txt
# from scheduler.daily_task import daily_task
# from scheduler.scheduler import start_scheduler
from server.app import app

def handler(event, context):
    # If the "source" is EventBridge, run the stock fetching logic
    # if event.get('source') == 'aws.events':
    #     print("Alarm went off! Fetching stock data...")
    #     daily_task()
    #     return {"status": "success"}
    
    # Otherwise, treat it like a normal web request for your Flask app
    return serverless_wsgi.handle_request(app, event, context)

# Keep this for local testing only
if __name__ == '__main__':
    # start_scheduler()
    app.run(debug=True, host='0.0.0.0', port=8080)