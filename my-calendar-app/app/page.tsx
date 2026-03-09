"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

// ── 型定義 ──────────────────────────────────────────────────
type CalendarEntry = {
  id: string;
  summary: string;
  backgroundColor?: string;
  primary?: boolean;
};

type CalendarTriad = {
  baseId: string;
  baseSummary: string;
  baseColor: string;
  penId?: string;
  penColor?: string;
  arcId?: string;
  arcColor?: string;
};

type StatusKey = "base" | "pen" | "arc";

type CalendarGroup = {
  id: string;
  name: string;
  color: string;
  baseIds: string[];
};

type CalendarEvent = {
  id: string;
  googleEventId: string;
  summary?: string;
  description?: string;
  start?: { dateTime?: string; date?: string };
  end?: { dateTime?: string; date?: string };
  calendarId: string;
  calendarSummary: string;
  calendarColor: string;
  statusKey: StatusKey;
};

type EventForm = {
  summary: string;
  calendarId: string;
  allDay: boolean;
  date: string;
  startTime: string;
  endTime: string;
  description: string;
};

type ModalState =
  | { mode: "create"; date: string }
  | { mode: "edit"; event: CalendarEvent };

// ── ステータス定義 ────────────────────────────────────────
const STATUS_LABELS: Record<StatusKey, string> = { base: "参加", pen: "未決定", arc: "不参加" };
const STATUS_COLORS: Record<StatusKey, string> = { base: "#16a34a", pen: "#d97706", arc: "#6b7280" };
const STATUS_ICONS: Record<StatusKey, string>  = { base: "✓", pen: "?", arc: "✗" };

// ── グループ API ─────────────────────────────────────────
async function fetchGroupsFromAPI(): Promise<CalendarGroup[]> {
  const res = await fetch("/api/groups");
  if (!res.ok) return [];
  const rows = await res.json();
  return (rows ?? []).map((r: Record<string, unknown>) => ({
    id:      r.id as string,
    name:    r.name as string,
    color:   r.color as string,
    baseIds: r.base_ids as string[],
  }));
}

async function createGroupAPI(g: Omit<CalendarGroup, "id">): Promise<CalendarGroup | null> {
  const res = await fetch("/api/groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(g),
  });
  if (!res.ok) return null;
  const rows = await res.json();
  const r = Array.isArray(rows) ? rows[0] : rows;
  return r ? { id: r.id, name: r.name, color: r.color, baseIds: r.base_ids } : null;
}

async function updateGroupAPI(id: string, g: Omit<CalendarGroup, "id">): Promise<void> {
  await fetch(`/api/groups/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(g),
  });
}

async function deleteGroupAPI(id: string): Promise<void> {
  await fetch(`/api/groups/${id}`, { method: "DELETE" });
}

// ── ユーティリティ ───────────────────────────────────────────
const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

function formatDateOnly(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function formatDisplayDate(s: string) {
  const [y, m, d] = s.split("-").map(Number);
  return `${m}/${d}(${WEEKDAYS[new Date(y, m - 1, d).getDay()]})`;
}
function formatTime(v?: string) {
  if (!v || !v.includes("T")) return "終日";
  const d = new Date(v);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function getEventDateKey(ev: CalendarEvent) {
  const v = ev.start?.dateTime || ev.start?.date;
  return v ? (v.includes("T") ? v.slice(0, 10) : v) : "";
}
function getEventSortKey(ev: CalendarEvent) { return ev.start?.dateTime || ev.start?.date || ""; }

function buildCalendarDays(month: Date): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const last  = new Date(month.getFullYear(), month.getMonth() + 1, 0);
  const start = new Date(first); start.setDate(start.getDate() - start.getDay());
  const end   = new Date(last);  end.setDate(end.getDate() + (6 - end.getDay()));
  const days: Date[] = [];
  const cur = new Date(start);
  while (cur <= end) { days.push(new Date(cur)); cur.setDate(cur.getDate() + 1); }
  return days;
}

function buildEventBody(form: EventForm) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const body: Record<string, unknown> = { summary: form.summary };
  if (form.description) body.description = form.description;
  if (form.allDay) {
    const end = new Date(form.date); end.setDate(end.getDate() + 1);
    body.start = { date: form.date };
    body.end   = { date: formatDateOnly(end) };
  } else {
    body.start = { dateTime: `${form.date}T${form.startTime}:00`, timeZone: tz };
    body.end   = { dateTime: `${form.date}T${form.endTime}:00`, timeZone: tz };
  }
  return body;
}

// ── _pen/_arc を検出してトライアドを構築 ──────────────────────
function parseTriads(calendars: CalendarEntry[]): CalendarTriad[] {
  const penMap = new Map<string, CalendarEntry>();
  const arcMap = new Map<string, CalendarEntry>();
  const bases: CalendarEntry[] = [];
  for (const c of calendars) {
    if (c.summary.endsWith("_pen")) penMap.set(c.summary.slice(0, -4), c);
    else if (c.summary.endsWith("_arc")) arcMap.set(c.summary.slice(0, -4), c);
    else bases.push(c);
  }
  return bases.map((b) => ({
    baseId: b.id, baseSummary: b.summary, baseColor: b.backgroundColor ?? "#4285F4",
    penId: penMap.get(b.summary)?.id, penColor: penMap.get(b.summary)?.backgroundColor,
    arcId: arcMap.get(b.summary)?.id, arcColor: arcMap.get(b.summary)?.backgroundColor,
  }));
}

function resolveCalendarIds(
  effectiveBaseIds: Set<string>,
  activeStatuses: Set<StatusKey>,
  triads: CalendarTriad[]
): string[] {
  const tm = new Map(triads.map((t) => [t.baseId, t]));
  const ids: string[] = [];
  for (const baseId of effectiveBaseIds) {
    const t = tm.get(baseId);
    if (!t) continue;
    if (activeStatuses.has("base")) ids.push(t.baseId);
    if (activeStatuses.has("pen") && t.penId) ids.push(t.penId);
    if (activeStatuses.has("arc") && t.arcId) ids.push(t.arcId);
  }
  return ids;
}

// ── グループ管理モーダル ─────────────────────────────────────
function GroupModal({
  triads, editGroup, onSave, onClose,
}: {
  triads: CalendarTriad[];
  editGroup: CalendarGroup | null;
  onSave: (action: "create" | "update" | "delete", g: CalendarGroup) => void;
  onClose: () => void;
}) {
  const [name, setName]   = useState(editGroup?.name ?? "");
  const [color, setColor] = useState(editGroup?.color ?? "#4285F4");
  const [sel, setSel]     = useState<Set<string>>(new Set(editGroup?.baseIds ?? []));

  const toggle = (id: string) =>
    setSel((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const save = () => {
    if (!name.trim() || sel.size === 0) return;
    const g: CalendarGroup = { id: editGroup?.id ?? "", name: name.trim(), color, baseIds: [...sel] };
    onSave(editGroup ? "update" : "create", g);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full sm:max-w-sm bg-white rounded-t-2xl sm:rounded-2xl shadow-2xl p-5 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-bold">{editGroup ? "グループを編集" : "グループを作成"}</h3>
          <button onClick={onClose} className="w-7 h-7 rounded-full bg-gray-100 text-gray-500 hover:bg-gray-200 flex items-center justify-center">✕</button>
        </div>
        <div className="mb-3 flex gap-2">
          <input className="flex-1 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="グループ名" value={name} onChange={(e) => setName(e.target.value)} />
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)}
            className="w-10 h-10 rounded-lg border border-gray-200 cursor-pointer" />
        </div>
        <p className="text-xs text-gray-500 mb-2">含めるカレンダーを選択</p>
        <div className="overflow-y-auto flex-1 space-y-1">
          {triads.map((t) => (
            <label key={t.baseId} className="flex items-center gap-2 p-2 rounded-lg hover:bg-gray-50 cursor-pointer">
              <input type="checkbox" checked={sel.has(t.baseId)} onChange={() => toggle(t.baseId)} className="rounded" />
              <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: t.baseColor }} />
              <span className="text-sm">{t.baseSummary}</span>
            </label>
          ))}
        </div>
        <div className="flex gap-2 mt-4">
          {editGroup && (
            <button onClick={() => onSave("delete", editGroup)}
              className="rounded-xl border border-red-300 px-3 py-2 text-sm text-red-600 hover:bg-red-50">削除</button>
          )}
          <div className="flex-1" />
          <button onClick={onClose} className="rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">キャンセル</button>
          <button onClick={save} disabled={!name.trim() || sel.size === 0}
            className="rounded-xl bg-blue-600 px-4 py-2 text-sm text-white font-semibold hover:bg-blue-700 disabled:opacity-50">保存</button>
        </div>
      </div>
    </div>
  );
}

// ── メインコンポーネント ─────────────────────────────────────
export default function Home() {
  const [calendars, setCalendars]             = useState<CalendarEntry[]>([]);
  const [selectedBaseIds, setSelectedBaseIds] = useState<Set<string>>(new Set());
  const [activeStatuses, setActiveStatuses]   = useState<Set<StatusKey>>(new Set(["base", "pen", "arc"]));
  const [groups, setGroups]                   = useState<CalendarGroup[]>([]);
  const [selectedGroupIds, setSelectedGroupIds] = useState<Set<string>>(new Set());
  const [showGroupModal, setShowGroupModal]   = useState(false);
  const [editingGroup, setEditingGroup]       = useState<CalendarGroup | null>(null);

  const [events, setEvents]     = useState<CalendarEvent[]>([]);
  const [loading, setLoading]   = useState(false);
  const [calLoading, setCalLoading] = useState(false);
  const [error, setError]       = useState("");
  const [currentMonth, setCurrentMonth] = useState(() => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), 1); });
  const [selectedDate, setSelectedDate] = useState(() => formatDateOnly(new Date()));

  const [modal, setModal]               = useState<ModalState | null>(null);
  const [form, setForm]                 = useState<EventForm | null>(null);
  const [saving, setSaving]             = useState(false);
  const [saveError, setSaveError]       = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  // 初回ロード
  useEffect(() => {
    fetchGroupsFromAPI().then(setGroups);
  }, []);

  const triads = useMemo(() => parseTriads(calendars), [calendars]);

  const allCalendarMap = useMemo(() => {
    const m = new Map<string, { summary: string; color: string; statusKey: StatusKey }>();
    for (const t of triads) {
      m.set(t.baseId, { summary: t.baseSummary, color: t.baseColor, statusKey: "base" });
      if (t.penId) m.set(t.penId, { summary: `${t.baseSummary}_pen`, color: t.penColor ?? t.baseColor, statusKey: "pen" });
      if (t.arcId) m.set(t.arcId, { summary: `${t.baseSummary}_arc`, color: t.arcColor ?? t.baseColor, statusKey: "arc" });
    }
    return m;
  }, [triads]);

  const primaryBaseId = useMemo(
    () => triads.find((t) => calendars.find((c) => c.id === t.baseId && c.primary))?.baseId ?? triads[0]?.baseId ?? "",
    [triads, calendars]
  );

  const effectiveBaseIds = useMemo(() => {
    const ids = new Set(selectedBaseIds);
    for (const gId of selectedGroupIds) groups.find((g) => g.id === gId)?.baseIds.forEach((id) => ids.add(id));
    return ids;
  }, [selectedBaseIds, selectedGroupIds, groups]);

  const calendarIdsToFetch = useMemo(
    () => resolveCalendarIds(effectiveBaseIds, activeStatuses, triads),
    [effectiveBaseIds, activeStatuses, triads]
  );

  // ── カレンダー一覧取得 ────────────────────────────────
  useEffect(() => {
    (async () => {
      setCalLoading(true);
      try {
        const res = await fetch("/api/calendar?action=list");
        if (!res.ok) throw new Error();
        const data = await res.json();
        const items: CalendarEntry[] = (data.items ?? []).map((c: Record<string, unknown>) => ({
          id: c.id as string,
          summary: (c.summary as string) || (c.id as string),
          backgroundColor: c.backgroundColor as string | undefined,
          primary: c.primary as boolean | undefined,
        }));
        setCalendars(items);
        const primaryId = items.find((c) => c.primary && !c.summary.endsWith("_pen") && !c.summary.endsWith("_arc"))?.id;
        if (primaryId) setSelectedBaseIds(new Set([primaryId]));
      } catch {
        setError("カレンダー一覧の取得に失敗しました");
      } finally {
        setCalLoading(false);
      }
    })();
  }, []);

  // ── イベント取得 ─────────────────────────────────────
  const fetchEvents = useCallback(async () => {
    if (calendarIdsToFetch.length === 0) { setEvents([]); return; }
    setLoading(true); setError("");
    try {
      const y = currentMonth.getFullYear(), m = currentMonth.getMonth();
      const timeMin = new Date(y, m, 1).toISOString();
      const timeMax = new Date(y, m + 1, 0, 23, 59, 59).toISOString();
      const res = await fetch(
        `/api/calendar?action=events&calendarIds=${encodeURIComponent(calendarIdsToFetch.join(","))}&timeMin=${encodeURIComponent(timeMin)}&timeMax=${encodeURIComponent(timeMax)}`
      );
      if (!res.ok) throw new Error();
      const results: { calendarId: string; items: Record<string, unknown>[] }[] = await res.json();
      const flat: CalendarEvent[] = results.flatMap(({ calendarId: calId, items }) => {
        const info = allCalendarMap.get(calId);
        return items.map((ev) => ({
          id: `${calId}__${ev.id as string}`,
          googleEventId: ev.id as string,
          summary: ev.summary as string | undefined,
          description: ev.description as string | undefined,
          start: ev.start as CalendarEvent["start"],
          end: ev.end as CalendarEvent["end"],
          calendarId: calId,
          calendarSummary: info?.summary ?? calId,
          calendarColor: info?.color ?? "#4285F4",
          statusKey: info?.statusKey ?? "base",
        }));
      });
      setEvents(flat.sort((a, b) => getEventSortKey(a).localeCompare(getEventSortKey(b))));
    } catch {
      setError("予定の取得に失敗しました");
    } finally {
      setLoading(false);
    }
  }, [currentMonth, calendarIdsToFetch, allCalendarMap]);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  // ── モーダル ──────────────────────────────────────────
  const openCreate = (date: string) => {
    setModal({ mode: "create", date });
    setForm({ summary: "", calendarId: primaryBaseId, allDay: false, date, startTime: "09:00", endTime: "10:00", description: "" });
    setSaveError(""); setConfirmDelete(false);
  };
  const openEdit = (ev: CalendarEvent) => {
    setModal({ mode: "edit", event: ev });
    setForm({
      summary: ev.summary ?? "", calendarId: ev.calendarId,
      allDay: !ev.start?.dateTime,
      date: ev.start?.date || ev.start?.dateTime?.slice(0, 10) || "",
      startTime: ev.start?.dateTime ? formatTime(ev.start.dateTime) : "09:00",
      endTime: ev.end?.dateTime ? formatTime(ev.end.dateTime) : "10:00",
      description: ev.description ?? "",
    });
    setSaveError(""); setConfirmDelete(false);
  };
  const closeModal = () => { setModal(null); setForm(null); setConfirmDelete(false); };
  const updateForm = (p: Partial<EventForm>) => setForm((prev) => (prev ? { ...prev, ...p } : prev));

  const saveEvent = async () => {
    if (!form) return;
    if (!form.summary.trim()) { setSaveError("タイトルを入力してください"); return; }
    setSaving(true); setSaveError("");
    try {
      const isCreate = modal?.mode === "create";
      const calId = isCreate ? form.calendarId : (modal as { mode: "edit"; event: CalendarEvent }).event.calendarId;
      const evId  = isCreate ? "" : (modal as { mode: "edit"; event: CalendarEvent }).event.googleEventId;
      const res = isCreate
        ? await fetch("/api/calendar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ calendarId: calId, event: buildEventBody(form) }),
          })
        : await fetch("/api/calendar", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ calendarId: calId, eventId: evId, event: buildEventBody(form) }),
          });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as Record<string, { message?: string }>).error?.message || "保存失敗");
      }
      closeModal(); await fetchEvents();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "保存に失敗しました");
    } finally { setSaving(false); }
  };

  const deleteEvent = async () => {
    if (modal?.mode !== "edit") return;
    const ev = modal.event;
    setSaving(true); setSaveError("");
    try {
      await fetch("/api/calendar", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ calendarId: ev.calendarId, eventId: ev.googleEventId }),
      });
      closeModal(); await fetchEvents();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "削除に失敗しました");
    } finally { setSaving(false); setConfirmDelete(false); }
  };

  const calendarDays = useMemo(() => buildCalendarDays(currentMonth), [currentMonth]);
  const eventsByDate = useMemo(() => {
    const map: Record<string, CalendarEvent[]> = {};
    for (const ev of events) { const k = getEventDateKey(ev); if (k) (map[k] ??= []).push(ev); }
    return map;
  }, [events]);
  const selectedEvents = useMemo(() => eventsByDate[selectedDate] ?? [], [eventsByDate, selectedDate]);
  const todayKey = useMemo(() => formatDateOnly(new Date()), []);

  const toggleBaseCalendar = (id: string) =>
    setSelectedBaseIds((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleGroup = (id: string) =>
    setSelectedGroupIds((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleStatus = (key: StatusKey) =>
    setActiveStatuses((p) => { const n = new Set(p); n.has(key) ? n.delete(key) : n.add(key); return n; });

  const handleGroupSave = async (action: "create" | "update" | "delete", g: CalendarGroup) => {
    if (action === "create") {
      const created = await createGroupAPI({ name: g.name, color: g.color, baseIds: g.baseIds });
      if (created) setGroups((p) => [...p, created]);
    } else if (action === "update") {
      await updateGroupAPI(g.id, { name: g.name, color: g.color, baseIds: g.baseIds });
      setGroups((p) => p.map((x) => (x.id === g.id ? g : x)));
    } else {
      await deleteGroupAPI(g.id);
      setGroups((p) => p.filter((x) => x.id !== g.id));
      setSelectedGroupIds((p) => { const n = new Set(p); n.delete(g.id); return n; });
    }
    setShowGroupModal(false);
    setEditingGroup(null);
  };

  // ── メイン ───────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b px-4 py-3 flex items-center justify-between sticky top-0 z-10 shadow-sm">
        <h1 className="text-lg font-bold text-gray-800">📅 My Calendar</h1>
      </header>

      {/* ステータスボタン + カレンダー選択 */}
      <div className="bg-white border-b px-4 py-3 space-y-2.5">
        {/* 参加 / 未決定 / 不参加 */}
        <div className="flex gap-2">
          {(["base", "pen", "arc"] as StatusKey[]).map((key) => {
            const active = activeStatuses.has(key);
            return (
              <button key={key} onClick={() => toggleStatus(key)}
                className={`flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm font-semibold border transition-all ${
                  active ? "text-white border-transparent" : "bg-white text-gray-400 border-gray-300"
                }`}
                style={active ? { backgroundColor: STATUS_COLORS[key] } : {}}>
                <span>{STATUS_ICONS[key]}</span> {STATUS_LABELS[key]}
              </button>
            );
          })}
        </div>

        {/* カレンダー選択 */}
        {calLoading ? (
          <p className="text-xs text-gray-400">カレンダー読み込み中...</p>
        ) : (
          <div className="flex flex-wrap gap-2 items-center">
            {triads.map((t) => {
              const checked = selectedBaseIds.has(t.baseId);
              return (
                <button key={t.baseId} onClick={() => toggleBaseCalendar(t.baseId)}
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-all ${
                    checked ? "border-transparent text-white" : "border-gray-300 text-gray-600 bg-white"
                  }`}
                  style={checked ? { backgroundColor: t.baseColor } : {}}>
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: t.baseColor }} />
                  {t.baseSummary}
                  {(t.penId || t.arcId) && <span className="text-[9px] opacity-60">▸</span>}
                </button>
              );
            })}

            {/* グループ */}
            {groups.map((g) => {
              const checked = selectedGroupIds.has(g.id);
              return (
                <button key={g.id} onClick={() => toggleGroup(g.id)}
                  onDoubleClick={() => { setEditingGroup(g); setShowGroupModal(true); }}
                  title="ダブルクリックで編集"
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-all ${
                    checked ? "border-transparent text-white" : "border-gray-300 text-gray-600 bg-white"
                  }`}
                  style={checked ? { backgroundColor: g.color } : {}}>
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: g.color }} />
                  📁 {g.name}
                </button>
              );
            })}

            <button onClick={() => { setEditingGroup(null); setShowGroupModal(true); }}
              className="rounded-full border border-dashed border-gray-300 px-3 py-1 text-xs text-gray-400 hover:border-blue-400 hover:text-blue-500 transition-colors">
              + グループ
            </button>
          </div>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 mx-auto w-full max-w-7xl p-3 md:p-6 md:flex md:gap-6 md:items-start">
        <section className="bg-white rounded-2xl shadow-sm border p-4 md:flex-1">
          <div className="flex items-center justify-between mb-3">
            <button onClick={() => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1))}
              className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-gray-50 transition-colors">‹ 前月</button>
            <h2 className="text-base font-bold text-gray-800">{currentMonth.getFullYear()}年{currentMonth.getMonth() + 1}月</h2>
            <button onClick={() => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1))}
              className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-gray-50 transition-colors">次月 ›</button>
          </div>

          <div className="grid grid-cols-7 mb-1">
            {WEEKDAYS.map((w, i) => (
              <div key={w} className={`text-center text-xs font-bold py-1 ${i === 0 ? "text-red-500" : i === 6 ? "text-blue-500" : "text-gray-500"}`}>{w}</div>
            ))}
          </div>

          {loading && <p className="text-center text-sm text-gray-400 py-6">読み込み中...</p>}
          {error && <p className="text-center text-sm text-red-500 py-2">{error}</p>}

          <div className="grid grid-cols-7 gap-0.5">
            {calendarDays.map((day) => {
              const key = formatDateOnly(day);
              const dayEvents = eventsByDate[key] ?? [];
              const isCurrentMonth = day.getMonth() === currentMonth.getMonth();
              const isSelected = selectedDate === key;
              const isToday = todayKey === key;
              const dow = day.getDay();
              return (
                <div key={key}
                  className={["relative rounded-lg p-1 text-left transition-all cursor-pointer group",
                    "min-h-[58px] md:min-h-[88px]",
                    isCurrentMonth ? "bg-white hover:bg-blue-50" : "bg-gray-50",
                    isSelected ? "ring-2 ring-blue-500 bg-blue-50" : "",
                  ].join(" ")}
                  onClick={() => setSelectedDate(key)}>
                  <div className="flex items-start justify-between">
                    <span className={["flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold mb-0.5",
                      isToday ? "bg-blue-600 text-white"
                        : isCurrentMonth ? (dow === 0 ? "text-red-500" : dow === 6 ? "text-blue-500" : "text-gray-700")
                        : "text-gray-300",
                    ].join(" ")}>{day.getDate()}</span>
                    {isCurrentMonth && (
                      <button onClick={(e) => { e.stopPropagation(); openCreate(key); }}
                        className="opacity-0 group-hover:opacity-100 transition-opacity w-5 h-5 rounded-full bg-blue-500 text-white text-xs flex items-center justify-center hover:bg-blue-600">+</button>
                    )}
                  </div>
                  <div className="space-y-0.5">
                    {dayEvents.slice(0, 2).map((ev) => (
                      <div key={ev.id}
                        onClick={(e) => { e.stopPropagation(); setSelectedDate(key); openEdit(ev); }}
                        className="truncate rounded px-1 leading-4 text-[9px] md:text-[11px] text-white cursor-pointer hover:opacity-80 flex items-center gap-0.5"
                        style={{ backgroundColor: ev.calendarColor }}>
                        {ev.statusKey !== "base" && <span className="text-[8px] flex-shrink-0">{STATUS_ICONS[ev.statusKey]}</span>}
                        <span className="truncate">{ev.summary || "（タイトルなし）"}</span>
                      </div>
                    ))}
                    {dayEvents[2] && (
                      <div onClick={(e) => { e.stopPropagation(); setSelectedDate(key); openEdit(dayEvents[2]); }}
                        className="hidden md:flex truncate rounded px-1 leading-4 text-[11px] text-white cursor-pointer hover:opacity-80 items-center gap-0.5"
                        style={{ backgroundColor: dayEvents[2].calendarColor }}>
                        {dayEvents[2].statusKey !== "base" && <span className="text-[9px] flex-shrink-0">{STATUS_ICONS[dayEvents[2].statusKey]}</span>}
                        <span className="truncate">{dayEvents[2].summary || "（タイトルなし）"}</span>
                      </div>
                    )}
                    {dayEvents.length > 2 && <div className="text-[9px] text-gray-400 pl-1 md:hidden">+{dayEvents.length - 2}</div>}
                    {dayEvents.length > 3 && <div className="hidden md:block text-[10px] text-gray-400 pl-1">+{dayEvents.length - 3}</div>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="mt-4 md:mt-0 md:w-72 lg:w-96 bg-white rounded-2xl shadow-sm border p-4 md:sticky md:top-20">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-bold text-gray-800">{formatDisplayDate(selectedDate)} の予定</h2>
            <button onClick={() => openCreate(selectedDate)}
              className="flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white font-semibold hover:bg-blue-700 transition-colors">
              <span className="text-base leading-none">+</span> 追加
            </button>
          </div>
          {selectedEvents.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8">予定はありません</p>
          ) : (
            <ul className="space-y-2">
              {selectedEvents.map((ev) => (
                <li key={ev.id} onClick={() => openEdit(ev)}
                  className="rounded-xl border border-gray-100 bg-gray-50 p-3 cursor-pointer hover:bg-blue-50 hover:border-blue-200 transition-colors">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: ev.calendarColor }} />
                    <span className="text-[10px] text-gray-400 truncate">{ev.calendarSummary}</span>
                    {ev.statusKey !== "base" && (
                      <span className="ml-auto text-[9px] rounded-full px-1.5 py-0.5 font-semibold text-white flex-shrink-0"
                        style={{ backgroundColor: STATUS_COLORS[ev.statusKey] }}>
                        {STATUS_LABELS[ev.statusKey]}
                      </span>
                    )}
                  </div>
                  <p className="font-semibold text-sm text-gray-800 leading-snug">{ev.summary || "（タイトルなし）"}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    {!ev.start?.dateTime ? "終日" : `${formatTime(ev.start.dateTime)} 〜 ${formatTime(ev.end?.dateTime)}`}
                  </p>
                  {ev.description && <p className="text-xs text-gray-400 mt-1 line-clamp-2">{ev.description}</p>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* イベント編集モーダル */}
      {modal && form && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40"
          onClick={(e) => { if (e.target === e.currentTarget) closeModal(); }}>
          <div className="w-full sm:max-w-md bg-white rounded-t-2xl sm:rounded-2xl shadow-2xl p-5 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-bold text-gray-800">{modal.mode === "create" ? "予定を追加" : "予定を編集"}</h3>
              <button onClick={closeModal} className="w-8 h-8 rounded-full bg-gray-100 flex items-center justify-center text-gray-500 hover:bg-gray-200">✕</button>
            </div>
            <div className="mb-3">
              <input type="text" placeholder="タイトル（必須）" value={form.summary}
                onChange={(e) => updateForm({ summary: e.target.value })} autoFocus
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            {modal.mode === "create" && (
              <div className="mb-3">
                <select value={form.calendarId} onChange={(e) => updateForm({ calendarId: e.target.value })}
                  className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                  {triads.map((t) => <option key={t.baseId} value={t.baseId}>{t.baseSummary}</option>)}
                </select>
              </div>
            )}
            <div className="mb-3">
              <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-gray-700">
                <div onClick={() => updateForm({ allDay: !form.allDay })}
                  className={`w-10 h-6 rounded-full transition-colors flex items-center px-0.5 ${form.allDay ? "bg-blue-500" : "bg-gray-300"}`}>
                  <div className={`w-5 h-5 rounded-full bg-white shadow transition-transform ${form.allDay ? "translate-x-4" : "translate-x-0"}`} />
                </div>
                終日
              </label>
            </div>
            <div className="mb-3">
              <input type="date" value={form.date} onChange={(e) => updateForm({ date: e.target.value })}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            {!form.allDay && (
              <div className="mb-3 flex gap-2 items-center">
                <input type="time" value={form.startTime} onChange={(e) => updateForm({ startTime: e.target.value })}
                  className="flex-1 rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                <span className="text-gray-400 text-sm">〜</span>
                <input type="time" value={form.endTime} onChange={(e) => updateForm({ endTime: e.target.value })}
                  className="flex-1 rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
            )}
            <div className="mb-4">
              <textarea placeholder="内容（任意）" value={form.description} rows={3}
                onChange={(e) => updateForm({ description: e.target.value })}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none" />
            </div>
            {saveError && <p className="text-sm text-red-500 mb-3">{saveError}</p>}
            <div className="flex gap-2">
              {modal.mode === "edit" && !confirmDelete && (
                <button onClick={() => setConfirmDelete(true)} disabled={saving}
                  className="rounded-xl border border-red-300 px-4 py-2.5 text-sm text-red-600 font-medium hover:bg-red-50 disabled:opacity-50">削除</button>
              )}
              {confirmDelete && (
                <button onClick={deleteEvent} disabled={saving}
                  className="rounded-xl bg-red-500 px-4 py-2.5 text-sm text-white font-semibold hover:bg-red-600 disabled:opacity-50">
                  {saving ? "削除中..." : "本当に削除"}
                </button>
              )}
              <div className="flex-1" />
              <button onClick={closeModal} disabled={saving}
                className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50">キャンセル</button>
              <button onClick={saveEvent} disabled={saving}
                className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm text-white font-semibold hover:bg-blue-700 disabled:opacity-50">
                {saving ? "保存中..." : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* グループ管理モーダル */}
      {showGroupModal && (
        <GroupModal triads={triads} editGroup={editingGroup}
          onSave={handleGroupSave}
          onClose={() => { setShowGroupModal(false); setEditingGroup(null); }} />
      )}
    </div>
  );
}
