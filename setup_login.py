import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from twitter_client import TwitterClient


def main():
    client = TwitterClient()
    if client.is_logged_in():
        yn = input("Already logged in. Re-login? (y/N): ")
        if yn.lower() != "y":
            print("OK, using existing session.")
            return
    client.login()


if __name__ == "__main__":
    main()
