"use client";

import { signIn, signOut, useSession } from "next-auth/react";
import { useEffect, useMemo, useState } from "react";

type CalendarEvent = {
  id: string;
  summary?: string;
  start?: { dateTime?: string; date?: string };
  end?: { dateTime?: string; date?: string };
};

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

function formatDateOnly(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function formatDisplayDate(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return `${m}/${d}(${WEEKDAYS[date.getDay()]})`;
}

function formatTime(value?: string): string {
  if (!value || !value.includes("T")) return "終日";
  const d = new Date(value);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function getEventDateKey(event: CalendarEvent): string {
  const value = event.start?.dateTime || event.start?.date;
  if (!value) return "";
  return value.includes("T") ? value.slice(0, 10) : value;
}

function buildCalendarDays(currentMonth: Date): Date[] {
  const firstDay = new Date(currentMonth.getFullYear(), currentMonth.getMonth(), 1);
  const lastDay = new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 0);
  const start = new Date(firstDay);
  start.setDate(start.getDate() - start.getDay());
  const end = new Date(lastDay);
  end.setDate(end.getDate() + (6 - end.getDay()));
  const days: Date[] = [];
  const cur = new Date(start);
  while (cur <= end) {
    days.push(new Date(cur));
    cur.setDate(cur.getDate() + 1);
  }
  return days;
}

export default function Home() {
  const { data: session } = useSession();
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [currentMonth, setCurrentMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [selectedDate, setSelectedDate] = useState(() => formatDateOnly(new Date()));

  useEffect(() => {
    if (!session?.accessToken) return;
    const fetchEvents = async () => {
      setLoading(true);
      setError("");
      try {
        const year = currentMonth.getFullYear();
        const month = currentMonth.getMonth();
        const timeMin = new Date(year, month, 1).toISOString();
        const timeMax = new Date(year, month + 1, 0, 23, 59, 59).toISOString();
        const res = await fetch(
          `https://www.googleapis.com/calendar/v3/calendars/primary/events?timeMin=${encodeURIComponent(timeMin)}&timeMax=${encodeURIComponent(timeMax)}&singleEvents=true&orderBy=startTime`,
          { headers: { Authorization: `Bearer ${session.accessToken}` } }
        );
        if (!res.ok) throw new Error("API error");
        const data = await res.json();
        setEvents(data.items ?? []);
      } catch {
        setError("予定の取得に失敗しました");
      } finally {
        setLoading(false);
      }
    };
    fetchEvents();
  }, [session, currentMonth]);

  const calendarDays = useMemo(() => buildCalendarDays(currentMonth), [currentMonth]);

  const eventsByDate = useMemo(() => {
    const map: Record<string, CalendarEvent[]> = {};
    for (const ev of events) {
      const key = getEventDateKey(ev);
      if (!key) continue;
      (map[key] ??= []).push(ev);
    }
    return map;
  }, [events]);

  const selectedEvents = useMemo(
    () => eventsByDate[selectedDate] ?? [],
    [eventsByDate, selectedDate]
  );

  const todayKey = useMemo(() => formatDateOnly(new Date()), []);

  // ─── ログイン画面 ───────────────────────────────────────────
  if (!session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
        <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-lg text-center space-y-5">
          <div className="text-4xl">📅</div>
          <h1 className="text-2xl font-bold text-gray-800">My Calendar</h1>
          <p className="text-gray-500 text-sm">Googleアカウントでログインしてください</p>
          <button
            onClick={() => signIn("google")}
            className="w-full rounded-xl bg-blue-600 px-6 py-3 text-white font-semibold hover:bg-blue-700 active:bg-blue-800 transition-colors"
          >
            Googleでログイン
          </button>
        </div>
      </main>
    );
  }

  // ─── メイン画面 ─────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b px-4 py-3 flex items-center justify-between sticky top-0 z-10 shadow-sm">
        <h1 className="text-lg font-bold text-gray-800">📅 My Calendar</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-500 hidden sm:block truncate max-w-[200px]">
            {session.user?.email}
          </span>
          <button
            onClick={() => signOut()}
            className="rounded-lg bg-gray-100 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-200 active:bg-gray-300 transition-colors whitespace-nowrap"
          >
            ログアウト
          </button>
        </div>
      </header>

      {/* Body: 1カラム(mobile) → 2カラム(md以上) */}
      <div className="flex-1 mx-auto w-full max-w-7xl p-3 md:p-6 md:flex md:gap-6 md:items-start">

        {/* ── カレンダーセクション ── */}
        <section className="bg-white rounded-2xl shadow-sm border p-4 md:flex-1">
          {/* 月ナビ */}
          <div className="flex items-center justify-between mb-3">
            <button
              onClick={() =>
                setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1))
              }
              className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-gray-50 active:bg-gray-100 transition-colors"
            >
              ‹ 前月
            </button>
            <h2 className="text-base font-bold text-gray-800">
              {currentMonth.getFullYear()}年{currentMonth.getMonth() + 1}月
            </h2>
            <button
              onClick={() =>
                setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1))
              }
              className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-gray-50 active:bg-gray-100 transition-colors"
            >
              次月 ›
            </button>
          </div>

          {/* 曜日ヘッダー */}
          <div className="grid grid-cols-7 mb-1">
            {WEEKDAYS.map((w, i) => (
              <div
                key={w}
                className={`text-center text-xs font-bold py-1 ${
                  i === 0 ? "text-red-500" : i === 6 ? "text-blue-500" : "text-gray-500"
                }`}
              >
                {w}
              </div>
            ))}
          </div>

          {loading && (
            <p className="text-center text-sm text-gray-400 py-6">読み込み中...</p>
          )}
          {error && (
            <p className="text-center text-sm text-red-500 py-2">{error}</p>
          )}

          {/* カレンダーグリッド */}
          <div className="grid grid-cols-7 gap-0.5">
            {calendarDays.map((day) => {
              const key = formatDateOnly(day);
              const dayEvents = eventsByDate[key] ?? [];
              const isCurrentMonth = day.getMonth() === currentMonth.getMonth();
              const isSelected = selectedDate === key;
              const isToday = todayKey === key;
              const dow = day.getDay();

              return (
                <button
                  key={key}
                  onClick={() => setSelectedDate(key)}
                  className={[
                    "relative rounded-lg p-1 text-left transition-all",
                    "min-h-[58px] md:min-h-[88px]",
                    isCurrentMonth ? "bg-white hover:bg-blue-50" : "bg-gray-50",
                    isSelected ? "ring-2 ring-blue-500 bg-blue-50" : "",
                  ].join(" ")}
                >
                  {/* 日付 */}
                  <span
                    className={[
                      "flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold mb-0.5",
                      isToday
                        ? "bg-blue-600 text-white"
                        : isCurrentMonth
                        ? dow === 0
                          ? "text-red-500"
                          : dow === 6
                          ? "text-blue-500"
                          : "text-gray-700"
                        : "text-gray-300",
                    ].join(" ")}
                  >
                    {day.getDate()}
                  </span>

                  {/* 予定バッジ */}
                  <div className="space-y-0.5">
                    {dayEvents.slice(0, 2).map((ev) => (
                      <div
                        key={ev.id}
                        className={[
                          "truncate rounded px-1 leading-4",
                          "text-[9px] md:text-[11px]",
                          isCurrentMonth
                            ? "bg-blue-100 text-blue-800"
                            : "bg-gray-100 text-gray-400",
                        ].join(" ")}
                      >
                        {ev.summary || "（タイトルなし）"}
                      </div>
                    ))}
                    {/* md以上は3件目も表示 */}
                    {dayEvents[2] && (
                      <div className="hidden md:block truncate rounded px-1 leading-4 text-[11px] bg-blue-100 text-blue-800">
                        {dayEvents[2].summary || "（タイトルなし）"}
                      </div>
                    )}
                    {/* +n */}
                    {dayEvents.length > 2 && (
                      <div className="text-[9px] text-gray-400 pl-1 md:hidden">
                        +{dayEvents.length - 2}
                      </div>
                    )}
                    {dayEvents.length > 3 && (
                      <div className="hidden md:block text-[10px] text-gray-400 pl-1">
                        +{dayEvents.length - 3}
                      </div>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        {/* ── 予定セクション ── */}
        <section className="mt-4 md:mt-0 md:w-72 lg:w-96 bg-white rounded-2xl shadow-sm border p-4 md:sticky md:top-20">
          <h2 className="text-base font-bold text-gray-800 mb-3">
            {formatDisplayDate(selectedDate)} の予定
          </h2>

          {selectedEvents.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8">予定はありません</p>
          ) : (
            <ul className="space-y-2">
              {selectedEvents.map((ev) => {
                const isAllDay = !ev.start?.dateTime;
                return (
                  <li key={ev.id} className="rounded-xl border border-gray-100 bg-gray-50 p-3">
                    <p className="font-semibold text-sm text-gray-800 leading-snug">
                      {ev.summary || "（タイトルなし）"}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                      {isAllDay
                        ? "終日"
                        : `${formatTime(ev.start?.dateTime)} 〜 ${formatTime(ev.end?.dateTime)}`}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
