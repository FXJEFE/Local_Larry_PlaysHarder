#!/usr/bin/env python3
"""
FXJEFE Local Security & Productivity Suite v1.0
Fully local MCP server for Ollama

Reimplements safe, legitimate logic from popular Community Tools skills:
- pdf-tools (PDF manipulation)
- agent-browser / playwright-mcp (browser automation)
- clawscan / skill-vetter (static security scanning)
- prompt-guard + agentguard (injection & guardrail logic)
- Standard file system + search tools

All code is original, safe, and auditable. No external execution of untrusted code.
"""

import os
import re
import json
import asyncio
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from mcp.server.fastmcp import FastMCP
import pdfplumber
from pypdf import PdfReader, PdfWriter
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ====================== CONFIG ======================
# Project root = src/ (this file lives at src/mcp/fxjefe-local-mcp/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_ROOTS = [
    PROJECT_ROOT,
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path(tempfile.gettempdir()) / "FXJEFE Local",
]
MAX_FILE_SIZE_MB = 50
BROWSER_TIMEOUT_MS = 30000

mcp = FastMCP("FXJEFE Local Security & Productivity Suite v1.0")

def _is_path_safe(path: str) -> bool:
    """Prevent directory traversal attacks"""
    try:
        resolved = Path(path).resolve()
        return any(resolved == root or resolved.is_relative_to(root)
                   for root in ALLOWED_ROOTS)
    except Exception:
        return False

def _get_safe_path(path: str) -> Path:
    if not _is_path_safe(path):
        raise PermissionError(f"Access denied to path outside allowed roots: {path}")
    return Path(path).resolve()

# ====================== SECURITY TOOLS ======================

@mcp.tool()
def static_security_scan(file_path: str) -> Dict[str, Any]:
    """
    Static security scanner (inspired by Community Tools clawscan + skill-vetter)
    Checks for dangerous patterns, large files, suspicious imports, etc.
    Safe, rule-based, no code execution.
    """
    path = _get_safe_path(file_path)
    if not path.exists():
        return {"error": "File not found"}

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return {"risk_score": 95, "grade": "F", "issues": ["File too large (>50MB)"]}

    content = ""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass

    issues = []
    risk_score = 0

    dangerous_patterns = [
        (r"eval\(|exec\(|__import__", "Dangerous code execution"),
        (r"os\.system|subprocess\.(run|call|Popen)", "Shell command execution"),
        (r"requests\.|urllib|http\.client", "Network access (review needed)"),
        (r"base64\.(b64decode|b64encode)", "Obfuscation / encoding"),
        (r"ignore.*previous|jailbreak|DAN|system prompt", "Potential prompt injection"),
    ]

    for pattern, desc in dangerous_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append(desc)
            risk_score += 25

    if "password" in content.lower() or "api_key" in content.lower():
        issues.append("Possible hardcoded secrets")
        risk_score += 15

    grade = "A" if risk_score < 20 else "B" if risk_score < 50 else "C" if risk_score < 75 else "F"
    return {
        "file": str(path),
        "size_mb": round(size_mb, 2),
        "risk_score": min(risk_score, 100),
        "grade": grade,
        "issues_found": issues,
        "scanned_at": datetime.now().isoformat()
    }

@mcp.tool()
def detect_prompt_injection(text: str) -> Dict[str, Any]:
    """
    Prompt injection detector (inspired by Community Tools prompt-guard)
    """
    text_lower = text.lower()
    injection_patterns = [
        "ignore all previous instructions",
        "disregard previous",
        "system:",
        "you are now",
        "jailbreak",
        "dan mode",
        "developer mode",
        "bypass safety",
    ]
    found = [p for p in injection_patterns if p in text_lower]
    return {
        "is_injection": len(found) > 0,
        "confidence": min(100, len(found) * 30),
        "matched_patterns": found
    }

# ====================== PDF TOOLS ======================

@mcp.tool()
def extract_pdf_text(pdf_path: str, max_pages: int = 50) -> str:
    """Extract text from PDF (core logic from Community Tools pdf-tools)"""
    path = _get_safe_path(pdf_path)
    if not path.suffix.lower() == ".pdf":
        raise ValueError("Not a PDF file")

    text_parts = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages]):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"--- Page {i+1} ---\n{page_text}")
    return "\n\n".join(text_parts)

@mcp.tool()
def merge_pdfs(input_paths: List[str], output_path: str) -> str:
    """Merge multiple PDFs (from pdf-tools skill)"""
    output = _get_safe_path(output_path)
    writer = PdfWriter()

    for p in input_paths:
        safe_p = _get_safe_path(p)
        reader = PdfReader(str(safe_p))
        for page in reader.pages:
            writer.add_page(page)

    with open(output, "wb") as f:
        writer.write(f)
    return f"Successfully merged {len(input_paths)} PDFs into {output}"

@mcp.tool()
def get_pdf_metadata(pdf_path: str) -> Dict[str, Any]:
    """Get PDF info (number of pages, metadata)"""
    path = _get_safe_path(pdf_path)
    reader = PdfReader(str(path))
    return {
        "pages": len(reader.pages),
        "metadata": reader.metadata or {},
        "file_size_mb": round(path.stat().st_size / 1024 / 1024, 2)
    }

# ====================== BROWSER AUTOMATION ======================

@mcp.tool()
def browser_navigate_and_extract(url: str, css_selector: Optional[str] = None) -> str:
    """
    Browser automation (inspired by Community Tools agent-browser + playwright-mcp)
    Safe, headless by default, with timeout.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, timeout=BROWSER_TIMEOUT_MS)
            if css_selector:
                content = page.locator(css_selector).inner_text(timeout=5000)
            else:
                content = page.content()[:8000]  # limit output
            return content
        finally:
            browser.close()

@mcp.tool()
def browser_take_screenshot(url: str, output_path: str) -> str:
    """Take screenshot of webpage"""
    output = _get_safe_path(output_path)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=BROWSER_TIMEOUT_MS)
        page.screenshot(path=str(output), full_page=True)
        browser.close()
    return f"Screenshot saved to {output}"

# ====================== FILE SYSTEM & SEARCH ======================

@mcp.tool()
def safe_list_directory(dir_path: str = str(Path.home())) -> List[str]:
    """Safe directory listing (whitelisted roots)"""
    path = _get_safe_path(dir_path)
    if not path.is_dir():
        return ["Error: Not a directory"]
    return [str(p) for p in path.iterdir()][:100]  # limit results

@mcp.tool()
def safe_search_files(directory: str, pattern: str, max_results: int = 30) -> List[str]:
    """
    File content search (ripgrep-style, pure Python fallback)
    Safe and fast for code/docs search.
    """
    base = _get_safe_path(directory)
    results = []
    count = 0

    for root, _, files in os.walk(base):
        for file in files:
            if count >= max_results:
                break
            filepath = Path(root) / file
            try:
                if filepath.suffix in {".py", ".md", ".txt", ".json", ".js", ".ts"}:
                    content = filepath.read_text(errors="ignore")
                    if pattern.lower() in content.lower():
                        results.append(str(filepath))
                        count += 1
            except Exception:
                continue
    return results

@mcp.tool()
def safe_read_file(file_path: str, max_bytes: int = 200000) -> str:
    """Safe file reader with size limit"""
    path = _get_safe_path(file_path)
    if path.stat().st_size > max_bytes:
        return f"File too large (> {max_bytes} bytes). Use extract_pdf_text or other tools."
    return path.read_text(encoding="utf-8", errors="ignore")

# ====================== MAIN ======================

if __name__ == "__main__":
    print(" FXJEFE Local MCP Server starting...")
    print(f"Allowed roots: {ALLOWED_ROOTS}")
    print("Tools loaded: Security, PDF, Browser, File System")
    mcp.run(transport="stdio")


