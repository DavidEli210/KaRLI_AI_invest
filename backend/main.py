import os
from dotenv import load_dotenv

from server.app import app
from scheduler.scheduler import start_scheduler

load_dotenv()

LISTEN_HOST = os.environ.get('LISTEN_HOST', 'localhost')
LISTEN_PORT = os.environ.get('LISTEN_PORT', '8080')

if __name__ == '__main__':
    # start_scheduler()
    app.run(debug=True, host=LISTEN_HOST, port=LISTEN_PORT)
