"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

export default function TrelloCallback() {
  const router = useRouter();
  const [status, setStatus] = useState<"processing" | "success" | "error">("processing");

  useEffect(() => {
    const hash = window.location.hash;
    const token = hash.match(/[#&]token=([^&]+)/)?.[1];
    if (!token) {
      setStatus("error");
      return;
    }
    fetch("/api/trello/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    })
      .then((res) => {
        if (res.ok) {
          setStatus("success");
          setTimeout(() => router.push("/?tab=tasks"), 1500);
        } else {
          setStatus("error");
        }
      })
      .catch(() => setStatus("error"));
  }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-white rounded-2xl shadow-lg p-10 text-center max-w-sm w-full">
        {status === "processing" && <p className="text-gray-600 text-sm">Trelloに接続中...</p>}
        {status === "success" && <p className="text-green-600 text-sm font-semibold">接続しました！画面を移動します...</p>}
        {status === "error" && (
          <>
            <p className="text-red-600 text-sm mb-4">接続に失敗しました</p>
            <button onClick={() => router.push("/")} className="text-blue-600 underline text-sm">戻る</button>
          </>
        )}
      </div>
    </div>
  );
}
