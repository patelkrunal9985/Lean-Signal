"""Pre-commit validation: run all test suites before committing.

Usage: python validate_pipeline.py
Returns exit code 0 if all tests pass, 1 if any fail."""

import sys, subprocess

TESTS = [
    ("Main test suite", [sys.executable, "test_strategies.py"]),
    ("Integration test", [sys.executable, "test_integration.py"]),
]

print("=" * 60)
print("PRE-COMMIT VALIDATION")
print("=" * 60)

all_passed = True

for name, cmd in TESTS:
    print(f"\n--- {name} ---")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    # Print last 5 lines of output showing test results
    lines = result.stdout.strip().splitlines()
    for line in lines[-5:]:
        print(f"  {line}")
    if result.stderr:
        stderr_lines = result.stderr.strip().splitlines()
        if stderr_lines:
            print(f"  (stderr: {stderr_lines[-1][:80]})")
    status = "OK" if result.returncode == 0 else "FAIL"
    print(f"  [{status}] {name} (exit code {result.returncode})")
    if result.returncode != 0:
        all_passed = False

print(f"\n{'=' * 60}")
if all_passed:
    print("ALL TESTS PASSED - ready to commit")
    sys.exit(0)
else:
    print("SOME TESTS FAILED - fix before committing")
    sys.exit(1)
