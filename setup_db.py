import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def create_database():
    try:
        # Connect without database first
        connection = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST', 'localhost'),
            user=os.getenv('MYSQL_USER', 'root'),
            password=os.getenv('MYSQL_PASSWORD', ''),
            port=int(os.getenv('MYSQL_PORT', 3307))
        )

        cursor = connection.cursor()

        # Create database if not exists
        db_name = os.getenv('MYSQL_DB', 'it_career_advisor')
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
        print(f"Database '{db_name}' created or already exists")

        cursor.close()
        connection.close()

    except Error as e:
        print(f"Error creating database: {e}")
        return False

    return True

def setup_tables():
    try:
        connection = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST', 'localhost'),
            user=os.getenv('MYSQL_USER', 'root'),
            password=os.getenv('MYSQL_PASSWORD', ''),
            port=int(os.getenv('MYSQL_PORT', 3307)), # ✅ เพิ่มบรรทัดนี้เข้ามา
            database=os.getenv('MYSQL_DB', 'it_career_advisor')
        )

        cursor = connection.cursor()

        # Read and execute schema.sql
        with open('schema.sql', 'r', encoding='utf-8') as f:
            sql_script = f.read()

        # Split by semicolon and execute each statement
        statements = [stmt.strip() for stmt in sql_script.split(';') if stmt.strip()]

        for statement in statements:
            if statement:
                cursor.execute(statement)
                print(f"Executed: {statement[:50]}...")

        connection.commit()
        print("All tables created successfully")

        cursor.close()
        connection.close()

    except Error as e:
        print(f"Error setting up tables: {e}")
        return False

    return True

if __name__ == "__main__":
    print("Setting up database...")
    if create_database():
        if setup_tables():
            print("Database setup completed successfully!")
        else:
            print("Failed to setup tables")
    else:
        print("Failed to create database")