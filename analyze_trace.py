import json
import sys

def analyze_trace(trace_path):
    with open(trace_path, 'r') as f:
        trace = json.load(f)

    events = trace.get('traceEvents', [])

    # We want to find long-running tasks on the main thread
    # Common event names: 'RunTask', 'FunctionCall', 'EvaluateScript'

    tasks = []
    for event in events:
        if event.get('name') in ['RunTask', 'FunctionCall', 'TimerFire', 'FireAnimationFrame']:
            dur = event.get('dur')
            if dur and dur > 10000: # > 10ms
                tasks.append(event)

    tasks.sort(key=lambda x: x.get('dur', 0), reverse=True)

    print(f"Total long tasks (>10ms) found: {len(tasks)}")
    for task in tasks[:20]:
        print(f"Task: {task['name']}, Duration: {task['dur']/1000:.2f}ms, Start: {task['ts']}")
        if 'args' in task and 'data' in task['args']:
            data = task['args']['data']
            if 'functionName' in data:
                print(f"  Function: {data['functionName']}, Source: {data.get('url', 'N/A')}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_trace.py <path_to_trace.json>")
        sys.exit(1)
    analyze_trace(sys.argv[1])
