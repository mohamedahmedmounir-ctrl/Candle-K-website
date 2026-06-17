"""Production WSGI entry point."""
import threading
from app import app, init_db, run_automation

# Initialize DB on startup
init_db()

# Start automation background thread
auto_thread = threading.Thread(target=run_automation, daemon=True)
auto_thread.start()

if __name__ == '__main__':
    app.run()
