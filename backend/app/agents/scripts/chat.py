from __future__ import annotations

import argparse

from app.agents import build_default_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gulf Breeze Air Agent CLI")
    parser.add_argument("message", nargs="?", help="Single-turn query")
    return parser.parse_args()


def run_single_turn(message: str) -> int:
    agent = build_default_agent()
    result = agent.run(message, channel="cli")

    print("Gulf Breeze Air Agent")
    print("")
    print(f"Session: {result.session_id}")
    print(f"Status: {result.status}")
    print(f"Handoff: {result.needs_handoff}")
    if result.handoff_reason:
        print(f"Handoff reason: {result.handoff_reason}")
    print("")
    print("Agent:")
    print(result.response)
    print("")
    print("Tools called:")
    if not result.tool_calls:
        print("- none")
    else:
        for call in result.tool_calls:
            print(f"- {call.tool_name} status={call.status} latency_ms={call.latency_ms}")
    return 0


def run_interactive() -> int:
    agent = build_default_agent()
    session_id = None
    print("Gulf Breeze Air Agent")
    print("Type 'exit' to quit.")
    print("")
    while True:
        user_message = input("You: ").strip()
        if user_message.lower() in {"exit", "quit"}:
            if session_id is not None:
                agent.close_session(session_id)
            return 0
        if not user_message:
            continue

        result = agent.run(
            user_message,
            channel="cli",
            session_id=session_id,
            finalize_session_on_return=False,
        )
        if session_id is None:
            session_id = result.session_id
            print(f"Session: {session_id}")
        print(f"Agent: {result.response}")
        if result.needs_handoff:
            print(f"(handoff: {result.handoff_reason})")


def main() -> int:
    args = parse_args()
    if args.message:
        return run_single_turn(args.message)
    return run_interactive()


if __name__ == "__main__":
    raise SystemExit(main())
