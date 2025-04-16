# Project Setup Guide

## Overview

This guide explains how to set up a Python virtual environment on macOS for this project. Using a virtual environment helps isolate project dependencies and ensures a clean, manageable development setup.

## Prerequisites

- macOS system
- Python 3 installed (preferably via Homebrew)
- Terminal access

---

## Step-by-Step Setup

### 1. Install Python 3 (if not already installed)

If you don't have Python 3 installed, you can install it using [Homebrew](https://brew.sh/):

```
brew install python
```

Verify the installation:

```
python3 --version
```

---

### 2. Navigate to Your Project Directory

Open Terminal and change to your project folder:

```
cd /path/to/your/project
```

---

### 3. Create a Virtual Environment

Create a new virtual environment named `myenv` (you can choose a different name):

```
python3 -m venv myenv
```

---

### 4. Activate the Virtual Environment

Activate the environment with:

```
source myenv/bin/activate
```

Your terminal prompt should change to indicate the environment is active.

---

### 5. Install Project Dependencies

Make sure your project has a `requirements.txt` file listing all necessary packages. Then install dependencies using:

```
pip install -r requirements.txt
```

---

### 6. Deactivate the Virtual Environment

When you finish working, deactivate the environment by running:

```
deactivate
```

---

## Additional Tips

- To update dependencies, modify `requirements.txt` and run the install command again.
- Keep your `requirements.txt` updated by running:

```
pip freeze > requirements.txt
```