from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def hello():
    # Use environment variable to show a message
    message = os.environ.get('APP_MESSAGE', 'Hello from my Python app!')
    return f'<h1>{message}</h1>'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
