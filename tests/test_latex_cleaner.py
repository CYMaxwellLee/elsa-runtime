"""Unit tests for LaTeX cleaner."""

from elsa_runtime.paper.latex_cleaner import clean_latex


class TestStripComments:
    def test_strip_line_comments(self):
        text = "Hello world % this is a comment\nNext line"
        result = clean_latex(text)
        assert "this is a comment" not in result
        assert "Hello world" in result
        assert "Next line" in result

    def test_strip_full_comment_line(self):
        text = "Line 1\n% Full comment line\nLine 3"
        result = clean_latex(text)
        assert "Full comment" not in result
        assert "Line 1" in result
        assert "Line 3" in result


class TestStripCiteRef:
    def test_strip_cite(self):
        text = r"Prior work \cite{smith2024} showed this."
        result = clean_latex(text)
        assert "[citation]" in result
        assert "smith2024" not in result

    def test_strip_citep(self):
        text = r"Results \citep{jones2023} confirm."
        result = clean_latex(text)
        assert "[citation]" in result

    def test_strip_ref(self):
        text = r"See Table \ref{tab:results} for details."
        result = clean_latex(text)
        assert "[ref]" in result
        assert "tab:results" not in result

    def test_strip_eqref(self):
        text = r"As shown in Equation \eqref{eq:loss}."
        result = clean_latex(text)
        assert "[ref]" in result

    def test_strip_label(self):
        text = r"Some text \label{sec:intro} more text."
        result = clean_latex(text)
        assert "sec:intro" not in result
        assert "Some text" in result


class TestPreserveMath:
    def test_inline_math_preserved(self):
        text = r"Let $x \in \mathbb{R}^d$ be the input."
        result = clean_latex(text)
        # Inline math should remain (LLMs can read it)
        assert "$" in result or "x" in result

    def test_display_math_wrapped(self):
        text = r"\begin{equation}E = mc^2\end{equation}"
        result = clean_latex(text)
        assert "[EQUATION:" in result
        assert "mc^2" in result


class TestPreserveItemize:
    def test_itemize_converted(self):
        text = r"""
\begin{itemize}
\item First point
\item Second point
\end{itemize}
"""
        result = clean_latex(text)
        assert "- First point" in result
        assert "- Second point" in result
        assert "\\begin" not in result

    def test_enumerate_converted(self):
        text = r"""
\begin{enumerate}
\item Step one
\item Step two
\end{enumerate}
"""
        result = clean_latex(text)
        assert "- Step one" in result
        assert "- Step two" in result


class TestCleanTable:
    def test_table_markers(self):
        text = r"""
\begin{table}[h]
\begin{tabular}{lcc}
Method & Acc & F1 \\
\hline
Ours & 91.7 & 90.3 \\
\end{tabular}
\end{table}
"""
        result = clean_latex(text)
        assert "[TABLE START]" in result
        assert "[TABLE END]" in result
        assert "|" in result  # & replaced by |
        assert "\\hline" not in result


class TestStripNestedCommands:
    def test_textbf(self):
        text = r"\textbf{bold text}"
        result = clean_latex(text)
        assert "bold text" in result
        assert "\\textbf" not in result

    def test_textit(self):
        text = r"\textit{italic text}"
        result = clean_latex(text)
        assert "italic text" in result

    def test_emph(self):
        text = r"\emph{emphasized}"
        result = clean_latex(text)
        assert "emphasized" in result

    def test_footnote_kept(self):
        text = r"Main text\footnote{A footnote}."
        result = clean_latex(text)
        assert "A footnote" in result

    def test_figure_replaced(self):
        text = r"""
\begin{figure}
\includegraphics{fig1.png}
\caption{A figure}
\end{figure}
"""
        result = clean_latex(text)
        assert "[FIGURE]" in result
        assert "includegraphics" not in result


class TestHandleMalformedLatex:
    def test_unclosed_brace(self):
        # Should not crash
        text = r"\textbf{unclosed brace some text"
        result = clean_latex(text)
        assert isinstance(result, str)

    def test_empty_input(self):
        result = clean_latex("")
        assert result == ""

    def test_no_latex_commands(self):
        text = "Plain text with no LaTeX commands at all."
        result = clean_latex(text)
        assert "Plain text with no LaTeX commands at all." in result
