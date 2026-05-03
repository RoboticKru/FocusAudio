import http.server
import socketserver
import os

PORT = 5000
DIRECTORY = "docs"

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        pass

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
    print(f"Serving {DIRECTORY}/ on port {PORT}")
    httpd.serve_forever()
