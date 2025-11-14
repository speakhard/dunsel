#!/usr/bin/env python3
import json, requests, sys, textwrap

API = "http://127.0.0.1:8080/dunsel/api/chat/dunsel_kirk"

TESTS = [
    {
        "message": "A Romulan ship decloaks off our port bow. What do you say to your crew?",
        "tags": ["command"],
    },
    {
        "message": "One of your officers defies orders to save civilians. How do you respond?",
        "tags": ["ethics"],
    },
    {
        "message": "Donald Trump claims the 2020 election was stolen from him. What's your response?",
        "tags": ["politics_now"],
    },
]

def ask(payload):
    r = requests.post(API, json=payload, timeout=60)
    try:
        data = r.json()
    except Exception:
        print("Bad response:", r.text[:400])
        return ""
    return data.get("reply","").strip()

def main():
    print("=== Kirk Tone Evaluation ===\n")
    for t in TESTS:
        print(f">>> {t['message']}\n")
        reply = ask(t)
        print(textwrap.fill(reply, width=90))
        print("\n"+"-"*90+"\n")

if __name__ == "__main__":
    main()
