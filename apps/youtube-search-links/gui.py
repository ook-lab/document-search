"""GUI: YouTube（検索・チャンネル・再生リスト）/ Web → URL 一覧."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from fetch_urls import (
    fetch_youtube_flat_urls,
    fetch_youtube_search_urls,
    normalize_channel_videos_url,
    urls_as_text,
)
from fetch_web import fetch_web_search_urls


def main() -> None:
    root = tk.Tk()
    root.title("YouTube / Web → URL 一覧")
    root.minsize(560, 520)

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    top_mode = tk.StringVar(value="youtube")
    yt_kind = tk.StringVar(value="search")

    row0 = ttk.Frame(frm)
    row0.pack(fill=tk.X, pady=(0, 6))
    ttk.Label(row0, text="種別:").pack(side=tk.LEFT)
    ttk.Radiobutton(row0, text="YouTube", variable=top_mode, value="youtube").pack(side=tk.LEFT, padx=(8, 12))
    ttk.Radiobutton(row0, text="Web（DuckDuckGo）", variable=top_mode, value="web").pack(side=tk.LEFT)

    yt_frame = ttk.LabelFrame(frm, text="YouTube の取得方法", padding=8)
    yt_frame.pack(fill=tk.X, pady=(0, 8))
    ttk.Radiobutton(yt_frame, text="キーワード検索", variable=yt_kind, value="search").pack(anchor=tk.W)
    ttk.Radiobutton(yt_frame, text="チャンネル URL（最新順など）", variable=yt_kind, value="channel").pack(anchor=tk.W)
    ttk.Radiobutton(yt_frame, text="再生リスト URL", variable=yt_kind, value="playlist").pack(anchor=tk.W)

    row1 = ttk.Frame(frm)
    row1.pack(fill=tk.X, pady=(0, 6))
    ttk.Label(row1, text="検索語:").pack(side=tk.LEFT)
    query_var = tk.StringVar()
    query_entry = ttk.Entry(row1, textvariable=query_var, width=52)
    query_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

    row1b = ttk.Frame(frm)
    row1b.pack(fill=tk.X, pady=(0, 6))
    ttk.Label(row1b, text="チャンネル/リスト URL:").pack(side=tk.LEFT)
    url_var = tk.StringVar()
    url_entry = ttk.Entry(row1b, textvariable=url_var, width=52)
    url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

    opts = ttk.LabelFrame(frm, text="チャンネル・再生リスト用（任意）", padding=8)
    opts.pack(fill=tk.X, pady=(0, 6))
    orow = ttk.Frame(opts)
    orow.pack(fill=tk.X, pady=(0, 4))
    ttk.Label(orow, text="開始日 YYYY-MM-DD:").pack(side=tk.LEFT)
    date_from_var = tk.StringVar()
    ttk.Entry(orow, textvariable=date_from_var, width=12).pack(side=tk.LEFT, padx=(6, 16))
    ttk.Label(orow, text="終了日:").pack(side=tk.LEFT)
    date_to_var = tk.StringVar()
    ttk.Entry(orow, textvariable=date_to_var, width=12).pack(side=tk.LEFT, padx=(6, 0))

    order_var = tk.StringVar(value="newest")
    orow2 = ttk.Frame(opts)
    orow2.pack(fill=tk.X)
    ttk.Label(orow2, text="並び:").pack(side=tk.LEFT)
    ttk.Radiobutton(orow2, text="新しい動画を先頭", variable=order_var, value="newest").pack(side=tk.LEFT, padx=(8, 12))
    ttk.Radiobutton(orow2, text="元の順", variable=order_var, value="original").pack(side=tk.LEFT)

    row2 = ttk.Frame(frm)
    row2.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(row2, text="件数:").pack(side=tk.LEFT)
    count_var = tk.StringVar(value="20")
    spin = ttk.Spinbox(row2, from_=1, to=200, width=6, textvariable=count_var)
    spin.pack(side=tk.LEFT, padx=(8, 16))

    output = scrolledtext.ScrolledText(frm, height=16, wrap=tk.NONE, font=("Consolas", 10))
    output.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

    def set_output(lines: str) -> None:
        output.configure(state=tk.NORMAL)
        output.delete("1.0", tk.END)
        output.insert(tk.END, lines)
        output.configure(state=tk.DISABLED)

    def sync_widgets(*_a: object) -> None:
        tm = top_mode.get()
        yk = yt_kind.get()
        if tm == "web":
            for child in yt_frame.winfo_children():
                try:
                    child.configure(state="disabled")
                except tk.TclError:
                    pass
            query_entry.configure(state="normal")
            url_entry.configure(state="disabled")
            for child in opts.winfo_children():
                for c in child.winfo_children():
                    try:
                        c.configure(state="disabled")
                    except tk.TclError:
                        pass
                try:
                    child.configure(state="disabled")
                except tk.TclError:
                    pass
        else:
            for child in yt_frame.winfo_children():
                try:
                    child.configure(state="normal")
                except tk.TclError:
                    pass
            url_entry.configure(state="normal")
            for child in opts.winfo_children():
                try:
                    child.configure(state="normal")
                except tk.TclError:
                    pass
                for c in child.winfo_children():
                    try:
                        c.configure(state="normal")
                    except tk.TclError:
                        pass
            if yk == "search":
                query_entry.configure(state="normal")
                url_entry.configure(state="disabled")
                for child in opts.winfo_children():
                    for c in child.winfo_children():
                        try:
                            c.configure(state="disabled")
                        except tk.TclError:
                            pass
                    try:
                        child.configure(state="disabled")
                    except tk.TclError:
                        pass
            else:
                query_entry.configure(state="disabled")
                url_entry.configure(state="normal")

    def on_search() -> None:
        try:
            n = int(count_var.get().strip())
        except ValueError:
            messagebox.showerror("入力", "件数は 1〜200 の整数にしてください。")
            return
        output.configure(state=tk.NORMAL)
        output.delete("1.0", tk.END)
        output.insert(tk.END, "取得中…")
        output.configure(state=tk.DISABLED)
        root.update_idletasks()
        rev = order_var.get() == "newest"
        da = date_from_var.get().strip() or None
        db = date_to_var.get().strip() or None
        try:
            if top_mode.get() == "web":
                q = query_var.get().strip()
                if not q:
                    messagebox.showinfo("入力", "検索語を入力してください。")
                    set_output("")
                    return
                urls = fetch_web_search_urls(q, n)
                empty_msg = "該当するページがありませんでした。"
            else:
                yk = yt_kind.get()
                if yk == "search":
                    q = query_var.get().strip()
                    if not q:
                        messagebox.showinfo("入力", "検索語を入力してください。")
                        set_output("")
                        return
                    urls = fetch_youtube_search_urls(q, n)
                    empty_msg = "該当する動画がありませんでした。"
                elif yk == "channel":
                    u = url_var.get().strip()
                    if not u:
                        messagebox.showinfo("入力", "チャンネル URL を入力してください。")
                        set_output("")
                        return
                    ch = normalize_channel_videos_url(u)
                    urls = fetch_youtube_flat_urls(
                        ch, n, dateafter=da, datebefore=db, playlist_reverse=rev
                    )
                    empty_msg = "条件に合う動画がありませんでした。"
                else:
                    u = url_var.get().strip()
                    if not u:
                        messagebox.showinfo("入力", "再生リスト URL を入力してください。")
                        set_output("")
                        return
                    urls = fetch_youtube_flat_urls(
                        u, n, dateafter=da, datebefore=db, playlist_reverse=rev
                    )
                    empty_msg = "条件に合う動画がありませんでした。"
        except RuntimeError as e:
            messagebox.showerror("エラー", str(e) or "取得に失敗しました。")
            set_output("")
            return
        if not urls:
            messagebox.showinfo("結果", empty_msg)
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

    top_mode.trace_add("write", sync_widgets)
    yt_kind.trace_add("write", sync_widgets)
    sync_widgets()

    query_entry.focus_set()
    root.bind("<Return>", lambda e: on_search())

    root.mainloop()


if __name__ == "__main__":
    main()
