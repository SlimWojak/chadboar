#!/bin/bash
# AutistBoar Health Check
# Validates environment, dependencies, API keys, and core guards
# Run on boot or when diagnosing system issues

set -e

WORKSPACE="/home/autistboar/autisticboar"
cd "$WORKSPACE"

echo "🐗 AutistBoar Health Check"
echo "=========================="
echo ""

# 1. Check Python version
echo "[1/8] Checking Python version..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 11 ]; then
        echo "  ✅ Python $PYTHON_VERSION (>= 3.11)"
    else
        echo "  ❌ Python $PYTHON_VERSION is too old (need >= 3.11)"
        exit 1
    fi
else
    echo "  ❌ Python 3 not found"
    exit 1
fi

# 2. Check venv exists
echo "[2/8] Checking virtual environment..."
if [ -d ".venv" ]; then
    echo "  ✅ .venv directory exists"
else
    echo "  ❌ .venv not found. Run: python3 -m venv .venv"
    exit 1
fi

# 3. Check dependencies installed
echo "[3/8] Checking dependencies..."
if .venv/bin/python3 -c "import pydantic, httpx, solana, yaml" 2>/dev/null; then
    echo "  ✅ Core dependencies installed"
else
    echo "  ❌ Missing dependencies. Run: .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# 4. Check .env file
echo "[4/8] Checking .env file..."
if [ -f ".env" ]; then
    echo "  ✅ .env file exists"
else
    echo "  ❌ .env not found. Copy .env.example and fill in API keys."
    exit 1
fi

# 5. Check API keys
echo "[5/8] Checking API keys..."
source .env
MISSING_KEYS=0

for KEY in HELIUS_API_KEY BIRDEYE_API_KEY NANSEN_API_KEY X_API_BEARER_TOKEN; do
    VAL="${!KEY}"
    if [ -z "$VAL" ] || [[ "$VAL" == *"your_"* ]]; then
        echo "  ❌ $KEY missing or placeholder"
        MISSING_KEYS=1
    else
        echo "  ✅ $KEY set (${#VAL} chars)"
    fi
done

if [ $MISSING_KEYS -eq 1 ]; then
    echo "  ⚠️  Some API keys missing. Skills may fail."
fi

# 6. Check skills directory
echo "[6/8] Checking skills..."
EXPECTED_SKILLS=("oracle_query" "warden_check" "narrative_scan" "execute_swap" "bead_write" "bead_query")
for SKILL in "${EXPECTED_SKILLS[@]}"; do
    if [ -f "lib/skills/${SKILL}.py" ]; then
        echo "  ✅ ${SKILL}.py"
    else
        echo "  ❌ ${SKILL}.py missing"
        exit 1
    fi
done

# 7. Check guards
echo "[7/8] Checking guards..."
for GUARD in killswitch drawdown risk; do
    if [ -f "lib/guards/${GUARD}.py" ]; then
        echo "  ✅ ${GUARD}.py"
    else
        echo "  ❌ ${GUARD}.py missing"
        exit 1
    fi
done

# 8. Test guard execution
echo "[8/8] Testing guard execution..."
if .venv/bin/python3 -m lib.guards.killswitch > /dev/null 2>&1; then
    echo "  ✅ Guards executable"
else
    echo "  ❌ Guards failed to execute"
    exit 1
fi

echo ""
echo "=========================="
echo "✅ All health checks passed"
echo "=========================="
exit 0
