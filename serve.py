"""Simple HTTP server with no-cache headers to always serve fresh files."""
import http.server
import socketserver
import os

PORT = 8000

class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Change to the directory where this script is located
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        super().__init__(*args, **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, format, *args):
        print(f'{self.address_string()} - {format % args}')

with socketserver.TCPServer(('', PORT), NoCacheHandler) as httpd:
    print(f'Serving on port {PORT} (no-cache mode)...')
    httpd.serve_forever()
