---
name: playwright-stealth-async-setup
description: Guides on correctly importing and using Playwright Stealth's asynchronous API, addressing common ModuleNotFoundError for 'playwright_stealth.async_api'. Use when encountering issues with async Playwright Stealth setup.
---

# Playwright Stealth Async Setup Guide

This guide provides instructions on how to correctly import and use the Playwright Stealth library with Playwright's asynchronous API, specifically addressing the `ModuleNotFoundError: No module named 'playwright_stealth.async_api'`.

## Problem

When working with Playwright in an asynchronous Python environment (e.g., with FastAPI/Uvicorn), the standard import for Playwright Stealth might fail. You might encounter an error like:

```
ModuleNotFoundError: No module named 'playwright_stealth.async_api'
```

Or, if you used an incorrect import, you might get a `NameError` when trying to call an async version of the stealth function.

## Solution

The `playwright-stealth` library requires a specific import path for its asynchronous functionality.

### Correct Import Statement

Ensure you are using the following import statement in your Python file:

```python
from playwright_stealth.async_api import stealth
```

### Correct Usage

When applying stealth to a Playwright page object in an asynchronous context, use `await stealth(self.page)`:

```python
# Assuming `self.page` is a Playwright Page object
await stealth(self.page)
```

### Prerequisites

Make sure the `playwright-stealth` library is installed in your Python environment:

```bash
pip install playwright-stealth
```

Or, if using `python3` specifically:

```bash
python3 -m pip install playwright-stealth
```

### Example Context

This issue was encountered and resolved in the `iclasspro.py` file during the development of the web dashboard for the iClassPro Enrollment Bot. The fix involved changing the import statement and the function call to use the asynchronous API correctly.
