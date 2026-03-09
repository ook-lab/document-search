"use client";

import { signIn, signOut, useSession } from "next-auth/react";
import { useCallback, useEffect, useMemo, useState } from "react";

// ─────────────────────────────────────────────────────────
// ユーザーごとの許可カレンダー設定
// '*' = すべて表示（管理者）
// string[] = 許可するカレンダーIDの一覧
// ─────────────────────────────────────────────────────────
const ALLOWED_CALENDARS_BY_USER: Record<string, "*" | string[]> = {
  "ookubo.y@workspace-o.com": "*", // パパ（管理者）= 全カレンダー
  // 以下、専用Googleアカウント作成後に追加
  // "mama@example.com":     ["family-common-id", "mama-calendar-id"],
  // "daughter@example.com": ["family-common-id", "daughter-calendar-id"],
  // "son@example.com":      ["family-common-id", "son-calendar-id"],
};

// ─────────────────────────────────────────────────────────
// 型定義
// ─────────────────────────────────────────────────────────
type CalendarEntry = {
  id: string;
  summary: string;
  backgroundColor?: string;
  primary?: boolean;
};

type CalendarEvent = {
  id: string;             // composite: `${calendarId}__${googleEventId}`
  googleEventId: string;  // Google Calendar の元の eventId
  summary?: string;
  description?: string;
  start?: { dateTime?: string; date?: string };
  end?: { dateTime?: string; date?: string };
  calendarId: string;
  calendarSummary: string;
  calendarColor: string;
};

type EventForm = {
  summary: string;
  calendarId: string;
  allDay: boolean;
  date: string;      // YYYY-MM-DD
  startTime: string; // HH:MM
  endTime: string;   // HH:MM
  description: string;
};

type ModalState =
  | { mode: "create"; date: string }
  | { mode: "edit"; event: CalendarEvent };

// ─────────────────────────────────────────────────────────
// ユーティリティ
// ─────────────────────────────────────────────────────────
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

function getEventSortKey(event: CalendarEvent): string {
  return event.start?.dateTime || event.start?.date || "";
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

function buildEventBody(form: EventForm) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const body: Record<string, unknown> = { summary: form.summary };
  if (form.description) body.description = form.description;
  if (form.allDay) {
    // Google Calendar の終日: end は翌日（exclusive）
    const endDate = new Date(form.date);
    endDate.setDate(endDate.getDate() + 1);
    body.start = { date: form.date };
    body.end = { date: formatDateOnly(endDate) };
  } else {
    body.start = { dateTime: `${form.date}T${form.startTime}:00`, timeZone: tz };
    body.end = { dateTime: `${form.date}T${form.endTime}:00`, timeZone: tz };
  }
  return body;
}

function initFormForCreate(date: string, primaryCalendarId: string): EventForm {
  return {
    summary: "",
    calendarId: primaryCalendarId,
    allDay: false,
    date,
    startTime: "09:00",
    endTime: "10:00",
    description: "",
  };
}

function initFormForEdit(ev: CalendarEvent): EventForm {
  const allDay = !ev.start?.dateTime;
  const date = ev.start?.date || ev.start?.dateTime?.slice(0, 10) || "";
  const startTime = ev.start?.dateTime ? formatTime(ev.start.dateTime) : "09:00";
  let endTime = ev.end?.dateTime ? formatTime(ev.end.dateTime) : "10:00";
  // 終日の場合は end が翌日なので補正不要（表示用なので startTime/endTime は使わない）
  return {
    summary: ev.summary ?? "",
    calendarId: ev.calendarId,
    allDay,
    date,
    startTime,
    endTime,
    description: ev.description ?? "",
  };
}

// ─────────────────────────────────────────────────────────
// メインコンポーネント
// ─────────────────────────────────────────────────────────
export default function Home() {
  const { data: session } = useSession();
  const [calendars, setCalendars] = useState<CalendarEntry[]>([]);
  const [selectedCalendarIds, setSelectedCalendarIds] = useState<Set<string>>(new Set());
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [calLoading, setCalLoading] = useState(false);
  const [error, setError] = useState("");
  const [currentMonth, setCurrentMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [selectedDate, setSelectedDate] = useState(() => formatDateOnly(new Date()));

  // モーダル
  const [modal, setModal] = useState<ModalState | null>(null);
  const [form, setForm] = useState<EventForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const userEmail = session?.user?.email ?? "";
  const allowedRule = ALLOWED_CALENDARS_BY_USER[userEmail];
  const isUnauthorized = !!session && allowedRule === undefined;

  const allowedCalendars = useMemo(() => {
    if (allowedRule === "*") return calendars;
    if (Array.isArray(allowedRule)) return calendars.filter((c) => allowedRule.includes(c.id));
    return [];
  }, [calendars, allowedRule]);

  const primaryCalendarId = useMemo(
    () => allowedCalendars.find((c) => c.primary)?.id ?? allowedCalendars[0]?.id ?? "",
    [allowedCalendars]
  );

  // ── カレンダー一覧取得 ────────────────────────────────
  useEffect(() => {
    if (!session?.accessToken || isUnauthorized) return;
    const fetch_ = async () => {
      setCalLoading(true);
      try {
        const res = await fetch(
          "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=250",
          { headers: { Authorization: `Bearer ${session.accessToken}` } }
        );
        if (!res.ok) throw new Error("calendarList error");
        const data = await res.json();
        const items: CalendarEntry[] = (data.items ?? []).map((c: Record<string, unknown>) => ({
          id: c.id as string,
          summary: (c.summary as string) || (c.id as string),
          backgroundColor: c.backgroundColor as string | undefined,
          primary: c.primary as boolean | undefined,
        }));
        setCalendars(items);
        const primaryId = items.find((c) => c.primary)?.id;
        if (primaryId) setSelectedCalendarIds(new Set([primaryId]));
      } catch {
        setError("カレンダー一覧の取得に失敗しました");
      } finally {
        setCalLoading(false);
      }
    };
    fetch_();
  }, [session, isUnauthorized]);

  // ── イベント取得 ─────────────────────────────────────
  const fetchEvents = useCallback(async () => {
    if (!session?.accessToken || selectedCalendarIds.size === 0) {
      setEvents([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const year = currentMonth.getFullYear();
      const month = currentMonth.getMonth();
      const timeMin = new Date(year, month, 1).toISOString();
      const timeMax = new Date(year, month + 1, 0, 23, 59, 59).toISOString();

      const calMap = Object.fromEntries(
        allowedCalendars.map((c) => [c.id, { summary: c.summary, color: c.backgroundColor ?? "#4285F4" }])
      );

      const results = await Promise.all(
        [...selectedCalendarIds].map(async (calId) => {
          const res = await fetch(
            `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calId)}/events?timeMin=${encodeURIComponent(timeMin)}&timeMax=${encodeURIComponent(timeMax)}&singleEvents=true&orderBy=startTime&maxResults=500`,
            { headers: { Authorization: `Bearer ${session.accessToken}` } }
          );
          if (!res.ok) return [];
          const data = await res.json();
          return (data.items ?? []).map((ev: Record<string, unknown>) => ({
            id: `${calId}__${ev.id as string}`,
            googleEventId: ev.id as string,
            summary: ev.summary as string | undefined,
            description: ev.description as string | undefined,
            start: ev.start as CalendarEvent["start"],
            end: ev.end as CalendarEvent["end"],
            calendarId: calId,
            calendarSummary: calMap[calId]?.summary ?? calId,
            calendarColor: calMap[calId]?.color ?? "#4285F4",
          }));
        })
      );

      const allEvents = results.flat().sort((a, b) =>
        getEventSortKey(a).localeCompare(getEventSortKey(b))
      );
      setEvents(allEvents);
    } catch {
      setError("予定の取得に失敗しました");
    } finally {
      setLoading(false);
    }
  }, [session, currentMonth, selectedCalendarIds, allowedCalendars]);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  // ── モーダル操作 ──────────────────────────────────────
  const openCreate = (date: string) => {
    setModal({ mode: "create", date });
    setForm(initFormForCreate(date, primaryCalendarId));
    setSaveError("");
    setConfirmDelete(false);
  };

  const openEdit = (ev: CalendarEvent) => {
    setModal({ mode: "edit", event: ev });
    setForm(initFormForEdit(ev));
    setSaveError("");
    setConfirmDelete(false);
  };

  const closeModal = () => {
    setModal(null);
    setForm(null);
    setConfirmDelete(false);
  };

  const updateForm = (patch: Partial<EventForm>) =>
    setForm((prev) => (prev ? { ...prev, ...patch } : prev));

  // ── 保存（作成 or 更新）─────────────────────────────
  const saveEvent = async () => {
    if (!form || !session?.accessToken) return;
    if (!form.summary.trim()) { setSaveError("タイトルを入力してください"); return; }
    setSaving(true);
    setSaveError("");
    try {
      const body = buildEventBody(form);
      let url: string;
      let method: string;
      if (modal?.mode === "create") {
        url = `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(form.calendarId)}/events`;
        method = "POST";
      } else {
        const ev = (modal as { mode: "edit"; event: CalendarEvent }).event;
        url = `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(ev.calendarId)}/events/${encodeURIComponent(ev.googleEventId)}`;
        method = "PUT";
      }
      const res = await fetch(url, {
        method,
        headers: {
          Authorization: `Bearer ${session.accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as Record<string, {message?: string}>).error?.message || "保存失敗");
      }
      closeModal();
      await fetchEvents();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "保存に失敗しました");
    } finally {
      setSaving(false);
    }
  };

  // ── 削除 ─────────────────────────────────────────────
  const deleteEvent = async () => {
    if (modal?.mode !== "edit" || !session?.accessToken) return;
    const ev = modal.event;
    setSaving(true);
    setSaveError("");
    try {
      const res = await fetch(
        `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(ev.calendarId)}/events/${encodeURIComponent(ev.googleEventId)}`,
        { method: "DELETE", headers: { Authorization: `Bearer ${session.accessToken}` } }
      );
      if (!res.ok && res.status !== 204) throw new Error("削除失敗");
      closeModal();
      await fetchEvents();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "削除に失敗しました");
    } finally {
      setSaving(false);
      setConfirmDelete(false);
    }
  };

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

  const toggleCalendar = (id: string) => {
    setSelectedCalendarIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  // ─── ログイン画面 ───────────────────────────────────────
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

  // ─── 権限なし画面 ────────────────────────────────────────
  if (isUnauthorized) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
        <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-lg text-center space-y-5">
          <div className="text-4xl">🚫</div>
          <h2 className="text-lg font-bold text-gray-800">アクセス権限がありません</h2>
          <p className="text-sm text-gray-500">{userEmail}</p>
          <button
            onClick={() => signOut()}
            className="w-full rounded-xl bg-gray-200 px-6 py-3 text-gray-700 font-semibold hover:bg-gray-300 transition-colors"
          >
            ログアウト
          </button>
        </div>
      </main>
    );
  }

  // ─── メイン画面 ─────────────────────────────────────────
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
            className="rounded-lg bg-gray-100 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-200 transition-colors whitespace-nowrap"
          >
            ログアウト
          </button>
        </div>
      </header>

      {/* カレンダー選択エリア */}
      <div className="bg-white border-b px-4 py-3">
        {calLoading ? (
          <p className="text-xs text-gray-400">カレンダー読み込み中...</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {allowedCalendars.map((cal) => {
              const checked = selectedCalendarIds.has(cal.id);
              return (
                <button
                  key={cal.id}
                  onClick={() => toggleCalendar(cal.id)}
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-all ${
                    checked ? "border-transparent text-white" : "border-gray-300 text-gray-600 bg-white"
                  }`}
                  style={checked ? { backgroundColor: cal.backgroundColor ?? "#4285F4" } : {}}
                >
                  <span
                    className="w-2 h-2 rounded-full flex-shrink-0"
                    style={{ backgroundColor: cal.backgroundColor ?? "#4285F4" }}
                  />
                  {cal.summary}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 mx-auto w-full max-w-7xl p-3 md:p-6 md:flex md:gap-6 md:items-start">

        {/* カレンダーセクション */}
        <section className="bg-white rounded-2xl shadow-sm border p-4 md:flex-1">
          {/* 月ナビ */}
          <div className="flex items-center justify-between mb-3">
            <button
              onClick={() => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1))}
              className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-gray-50 transition-colors"
            >
              ‹ 前月
            </button>
            <h2 className="text-base font-bold text-gray-800">
              {currentMonth.getFullYear()}年{currentMonth.getMonth() + 1}月
            </h2>
            <button
              onClick={() => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1))}
              className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-gray-50 transition-colors"
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

          {loading && <p className="text-center text-sm text-gray-400 py-6">読み込み中...</p>}
          {error && <p className="text-center text-sm text-red-500 py-2">{error}</p>}

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
                <div
                  key={key}
                  className={[
                    "relative rounded-lg p-1 text-left transition-all cursor-pointer group",
                    "min-h-[58px] md:min-h-[88px]",
                    isCurrentMonth ? "bg-white hover:bg-blue-50" : "bg-gray-50",
                    isSelected ? "ring-2 ring-blue-500 bg-blue-50" : "",
                  ].join(" ")}
                  onClick={() => setSelectedDate(key)}
                >
                  <div className="flex items-start justify-between">
                    <span
                      className={[
                        "flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold mb-0.5",
                        isToday
                          ? "bg-blue-600 text-white"
                          : isCurrentMonth
                          ? dow === 0 ? "text-red-500" : dow === 6 ? "text-blue-500" : "text-gray-700"
                          : "text-gray-300",
                      ].join(" ")}
                    >
                      {day.getDate()}
                    </span>
                    {/* + ボタン（ホバー or 選択中に表示） */}
                    {isCurrentMonth && (
                      <button
                        onClick={(e) => { e.stopPropagation(); openCreate(key); }}
                        className="opacity-0 group-hover:opacity-100 transition-opacity w-5 h-5 rounded-full bg-blue-500 text-white text-xs flex items-center justify-center flex-shrink-0 hover:bg-blue-600"
                        title="予定を追加"
                      >
                        +
                      </button>
                    )}
                  </div>

                  <div className="space-y-0.5">
                    {dayEvents.slice(0, 2).map((ev) => (
                      <div
                        key={ev.id}
                        onClick={(e) => { e.stopPropagation(); setSelectedDate(key); openEdit(ev); }}
                        className="truncate rounded px-1 leading-4 text-[9px] md:text-[11px] text-white cursor-pointer hover:opacity-80"
                        style={{ backgroundColor: ev.calendarColor }}
                      >
                        {ev.summary || "（タイトルなし）"}
                      </div>
                    ))}
                    {dayEvents[2] && (
                      <div
                        onClick={(e) => { e.stopPropagation(); setSelectedDate(key); openEdit(dayEvents[2]); }}
                        className="hidden md:block truncate rounded px-1 leading-4 text-[11px] text-white cursor-pointer hover:opacity-80"
                        style={{ backgroundColor: dayEvents[2].calendarColor }}
                      >
                        {dayEvents[2].summary || "（タイトルなし）"}
                      </div>
                    )}
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
                </div>
              );
            })}
          </div>
        </section>

        {/* 予定セクション */}
        <section className="mt-4 md:mt-0 md:w-72 lg:w-96 bg-white rounded-2xl shadow-sm border p-4 md:sticky md:top-20">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-bold text-gray-800">
              {formatDisplayDate(selectedDate)} の予定
            </h2>
            <button
              onClick={() => openCreate(selectedDate)}
              className="flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white font-semibold hover:bg-blue-700 transition-colors"
            >
              <span className="text-base leading-none">+</span> 追加
            </button>
          </div>

          {selectedEvents.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8">予定はありません</p>
          ) : (
            <ul className="space-y-2">
              {selectedEvents.map((ev) => {
                const isAllDay = !ev.start?.dateTime;
                return (
                  <li
                    key={ev.id}
                    onClick={() => openEdit(ev)}
                    className="rounded-xl border border-gray-100 bg-gray-50 p-3 cursor-pointer hover:bg-blue-50 hover:border-blue-200 transition-colors"
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <span
                        className="w-2 h-2 rounded-full flex-shrink-0"
                        style={{ backgroundColor: ev.calendarColor }}
                      />
                      <span className="text-[10px] text-gray-400 truncate">{ev.calendarSummary}</span>
                    </div>
                    <p className="font-semibold text-sm text-gray-800 leading-snug">
                      {ev.summary || "（タイトルなし）"}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                      {isAllDay
                        ? "終日"
                        : `${formatTime(ev.start?.dateTime)} 〜 ${formatTime(ev.end?.dateTime)}`}
                    </p>
                    {ev.description && (
                      <p className="text-xs text-gray-400 mt-1 line-clamp-2">{ev.description}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      {/* ─── イベント編集モーダル ─────────────────────────── */}
      {modal && form && (
        <div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40"
          onClick={(e) => { if (e.target === e.currentTarget) closeModal(); }}
        >
          <div className="w-full sm:max-w-md bg-white rounded-t-2xl sm:rounded-2xl shadow-2xl p-5 max-h-[90vh] overflow-y-auto">
            {/* モーダルヘッダー */}
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-bold text-gray-800">
                {modal.mode === "create" ? "予定を追加" : "予定を編集"}
              </h3>
              <button
                onClick={closeModal}
                className="w-8 h-8 rounded-full bg-gray-100 flex items-center justify-center text-gray-500 hover:bg-gray-200"
              >
                ✕
              </button>
            </div>

            {/* タイトル */}
            <div className="mb-3">
              <input
                type="text"
                placeholder="タイトル（必須）"
                value={form.summary}
                onChange={(e) => updateForm({ summary: e.target.value })}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                autoFocus
              />
            </div>

            {/* カレンダー選択（作成時のみ） */}
            {modal.mode === "create" && (
              <div className="mb-3">
                <select
                  value={form.calendarId}
                  onChange={(e) => updateForm({ calendarId: e.target.value })}
                  className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                >
                  {allowedCalendars.map((cal) => (
                    <option key={cal.id} value={cal.id}>{cal.summary}</option>
                  ))}
                </select>
              </div>
            )}

            {/* 終日トグル */}
            <div className="mb-3 flex items-center gap-2">
              <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-gray-700">
                <div
                  onClick={() => updateForm({ allDay: !form.allDay })}
                  className={`w-10 h-6 rounded-full transition-colors flex items-center px-0.5 ${
                    form.allDay ? "bg-blue-500" : "bg-gray-300"
                  }`}
                >
                  <div
                    className={`w-5 h-5 rounded-full bg-white shadow transition-transform ${
                      form.allDay ? "translate-x-4" : "translate-x-0"
                    }`}
                  />
                </div>
                終日
              </label>
            </div>

            {/* 日付 */}
            <div className="mb-3">
              <input
                type="date"
                value={form.date}
                onChange={(e) => updateForm({ date: e.target.value })}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            {/* 時刻（終日でない場合） */}
            {!form.allDay && (
              <div className="mb-3 flex gap-2 items-center">
                <input
                  type="time"
                  value={form.startTime}
                  onChange={(e) => updateForm({ startTime: e.target.value })}
                  className="flex-1 rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <span className="text-gray-400 text-sm">〜</span>
                <input
                  type="time"
                  value={form.endTime}
                  onChange={(e) => updateForm({ endTime: e.target.value })}
                  className="flex-1 rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            )}

            {/* 内容 */}
            <div className="mb-4">
              <textarea
                placeholder="内容（任意）"
                value={form.description}
                onChange={(e) => updateForm({ description: e.target.value })}
                rows={3}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              />
            </div>

            {/* エラー表示 */}
            {saveError && (
              <p className="text-sm text-red-500 mb-3">{saveError}</p>
            )}

            {/* ボタン */}
            <div className="flex gap-2">
              {modal.mode === "edit" && !confirmDelete && (
                <button
                  onClick={() => setConfirmDelete(true)}
                  disabled={saving}
                  className="rounded-xl border border-red-300 px-4 py-2.5 text-sm text-red-600 font-medium hover:bg-red-50 transition-colors disabled:opacity-50"
                >
                  削除
                </button>
              )}
              {confirmDelete && (
                <button
                  onClick={deleteEvent}
                  disabled={saving}
                  className="rounded-xl bg-red-500 px-4 py-2.5 text-sm text-white font-semibold hover:bg-red-600 transition-colors disabled:opacity-50"
                >
                  {saving ? "削除中..." : "本当に削除"}
                </button>
              )}
              <div className="flex-1" />
              <button
                onClick={closeModal}
                disabled={saving}
                className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm text-gray-700 font-medium hover:bg-gray-50 transition-colors disabled:opacity-50"
              >
                キャンセル
              </button>
              <button
                onClick={saveEvent}
                disabled={saving}
                className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm text-white font-semibold hover:bg-blue-700 transition-colors disabled:opacity-50"
              >
                {saving ? "保存中..." : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
