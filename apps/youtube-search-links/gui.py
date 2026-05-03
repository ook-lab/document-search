"""GUI: 検索語と件数から URL 一覧を表示し、クリップボードへコピー（NotebookLM 貼り付け用）."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from fetch_urls import fetch_youtube_search_urls, urls_as_text


def main() -> None:
    root = tk.Tk()
    root.title("YouTube 検索 → URL 一覧")
    root.minsize(520, 400)

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    row1 = ttk.Frame(frm)
    row1.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(row1, text="検索語:").pack(side=tk.LEFT)
    query_var = tk.StringVar()
    entry = ttk.Entry(row1, textvariable=query_var, width=50)
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

    row2 = ttk.Frame(frm)
    row2.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(row2, text="件数:").pack(side=tk.LEFT)
    count_var = tk.StringVar(value="20")
    spin = ttk.Spinbox(row2, from_=1, to=50, width=6, textvariable=count_var)
    spin.pack(side=tk.LEFT, padx=(8, 16))

    output = scrolledtext.ScrolledText(frm, height=18, wrap=tk.NONE, font=("Consolas", 10))
    output.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

    def set_output(lines: str) -> None:
        output.configure(state=tk.NORMAL)
        output.delete("1.0", tk.END)
        output.insert(tk.END, lines)
        output.configure(state=tk.DISABLED)

    def on_search() -> None:
        q = query_var.get().strip()
        if not q:
            messagebox.showinfo("入力", "検索語を入力してください。")
            return
        try:
            n = int(count_var.get().strip())
        except ValueError:
            messagebox.showerror("入力", "件数は 1〜50 の整数にしてください。")
            return
        output.configure(state=tk.NORMAL)
        output.delete("1.0", tk.END)
        output.insert(tk.END, "取得中…")
        output.configure(state=tk.DISABLED)
        root.update_idletasks()
        try:
            urls = fetch_youtube_search_urls(q, n)
        except RuntimeError as e:
            messagebox.showerror("エラー", str(e) or "取得に失敗しました。")
            set_output("")
            return
        if not urls:
            messagebox.showinfo("結果", "該当する動画がありませんでした。")
            set_output("")
            return
        set_output(urls_as_text(urls))

    def on_copy() -> None:
        text = output.get("1.0", tk.END).strip()
        if not text or text == "取得中…":
            messagebox.showinfo("コピー", "コピーする内容がありません。先に「取得」を実行してください。")
            return
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        status.configure(text="クリップボードにコピーしました（NotebookLM に貼り付け可）")
        root.after(4000, lambda: status.configure(text=""))

    row3 = ttk.Frame(frm)
    row3.pack(fill=tk.X)
    ttk.Button(row3, text="取得", command=on_search).pack(side=tk.LEFT)
    ttk.Button(row3, text="クリップボードにコピー", command=on_copy).pack(side=tk.LEFT, padx=(8, 0))
    status = ttk.Label(frm, text="")
    status.pack(anchor=tk.W, pady=(6, 0))

    entry.focus_set()
    root.bind("<Return>", lambda e: on_search())

    root.mainloop()


if __name__ == "__main__":
    main()
