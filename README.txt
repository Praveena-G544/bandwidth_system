1. In pgAdmin create database bandwidth_db, then run 1_schema.sql, then 2_seed_data.sql (Query Tool, F5).
2. Open this folder in VS Code. Put your postgres password in config.py.
3. python -m venv venv ; venv\Scripts\activate (Windows) / source venv/bin/activate (Mac/Linux)
4. pip install -r requirements.txt
5. python test_connection.py   (should say Connected OK)
6. python app.py  -> open http://127.0.0.1:5000
