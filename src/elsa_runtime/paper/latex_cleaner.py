"""
latex_cleaner.py — Strip LaTeX commands while preserving readable text.

Goal: turn LaTeX source into clean prose that an LLM can read.
Keep: text content, math in readable form, table data.
Remove: formatting commands, cross-references (numbers only), figure includes.
"""

import re


def clean_latex(text: str) -> str:
    """Clean LaTeX source into readable text."""

    # Order matters — apply these sequentially

    # 1. Remove comments (lines starting with %)
    text = re.sub(r"(?m)%.*$", "", text)

    # 2. Replace \textbf{...}, \textit{...}, \emph{...} with content
    text = re.sub(
        r"\\(?:textbf|textit|emph|underline|texttt)\{([^}]*)\}", r"\1", text
    )

    # 3. Replace \cite{...} with [citation]
    text = re.sub(r"\\cite[tp]?\{[^}]*\}", "[citation]", text)

    # 4. Replace \ref{...}, \eqref{...} with [ref]
    text = re.sub(r"\\(?:eq)?ref\{[^}]*\}", "[ref]", text)

    # 5. Replace \label{...} with nothing
    text = re.sub(r"\\label\{[^}]*\}", "", text)

    # 6. Replace inline math $...$ — keep content (LLMs can read simple math)

    # 7. Replace display math environments with content
    for env in [
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
    ]:
        escaped_env = re.escape(env)
        text = re.sub(
            rf"\\begin\{{{escaped_env}\}}(.*?)\\end\{{{escaped_env}\}}",
            r"[EQUATION: \1]",
            text,
            flags=re.DOTALL,
        )

    # 8. Replace \begin{itemize/enumerate}...\end{...} but keep \item text
    for env in ["itemize", "enumerate"]:
        text = re.sub(rf"\\begin\{{{env}\}}", "", text)
        text = re.sub(rf"\\end\{{{env}\}}", "", text)
    text = re.sub(r"\\item\s*", "- ", text)

    # 9. Remove figure environments but note their presence
    text = re.sub(
        r"\\begin\{figure\*?\}.*?\\end\{figure\*?\}",
        "[FIGURE]",
        text,
        flags=re.DOTALL,
    )

    # 10. Simplify table environments — keep tabular content
    text = re.sub(r"\\begin\{table\*?\}", "[TABLE START]", text)
    text = re.sub(r"\\end\{table\*?\}", "[TABLE END]", text)
    text = re.sub(r"\\begin\{tabular\}\{[^}]*\}", "", text)
    text = re.sub(r"\\end\{tabular\}", "", text)
    text = re.sub(r"\\hline", "", text)
    text = re.sub(r"\\toprule|\\midrule|\\bottomrule", "", text)
    text = text.replace("&", " | ")
    text = text.replace("\\\\", "\n")

    # 11. Remove remaining simple commands
    text = re.sub(
        r"\\(?:vspace|hspace|noindent|smallskip|medskip|bigskip|newline|clearpage|pagebreak)\b\*?(?:\{[^}]*\})?",
        "",
        text,
    )

    # 12. Remove \footnote{...} but keep content
    text = re.sub(r"\\footnote\{([^}]*)\}", r" (\1)", text)

    # 13. Remove leftover braces from unknown commands: \foo{bar} -> bar
    text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)

    # 14. Remove standalone commands: \foo -> nothing
    text = re.sub(r"\\[a-zA-Z]+\b", "", text)

    # 15. Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = text.strip()

    return text
