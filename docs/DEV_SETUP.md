## FancyBot Dev Setup (WSL + Tests)

This guide is for working on FancyBot from Windows using WSL, while staying inside the safety rails from `MUST_READ_BEFORE_MAKING_ANY_CHANGES.md`.

---

### 1. WSL Python & venv

From your WSL shell (not PowerShell or CMD):

```bash
cd ~/fancybot_revised

# Install Python tooling if needed
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

# Create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# Install project dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

Always run FancyBot and tests **from inside WSL with the venv active**.

---

### 2. Running the Test Suite

Tests live under `tests/` and are the primary guard rail for refactors.

```bash
cd ~/fancybot_revised
source .venv/bin/activate

# Run all tests
pytest

# Or run a focused file
pytest tests/test_phemex_common.py -q
```

If `pytest` is not found, ensure the venv is active and `requirements.txt` is installed.

---

### 3. Quick Sanity Checks After Edits

Follow the verification rules from `MUST_READ_BEFORE_MAKING_ANY_CHANGES.md`:

```bash
cd ~/fancybot_revised
source .venv/bin/activate

# Compile a single file you touched
python3 -m py_compile core/phemex_common.py

# Optionally run a single targeted test
pytest tests/test_phemex_common_indicators.py -q
```

Keep changes small and always make sure at least one relevant test is exercising your modification.

---

### 4. Running the Sim Bot from WSL

With the venv active:

```bash
cd ~/fancybot_revised
source .venv/bin/activate

python3 core/sim_bot.py --no-ai --no-entity --interval 60
```

Check the existing docs (`README.md`, `SYSTEM_ARCHITECTURE.md`, `SYSTEM_MECHANICS.txt`) for behavioral details. This file is only about **environment + workflow**, not trading logic.

