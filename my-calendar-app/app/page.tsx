"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import TaskDetailModal from "./components/TaskDetailModal";

// ── 型定義 ──────────────────────────────────────────────────
type TrelloLabel = { id: string; name: string; color: string };
type CheckItem   = { id: string; name: string; state: "complete" | "incomplete" };
type Checklist   = { id: string; name: string; checkItems: CheckItem[] };
type MemberData  = { id: string; name: string };

type Task = {
  id: string;
  cardName: string;
  description?: string;
  dueDate?: string;
  dueComplete?: boolean;
  assignees?: string[];
  labels?: TrelloLabel[];
  checklistTotal?: number;
  checklistDone?: number;
  checklists?: Checklist[];
  membersData?: MemberData[];
  calendarGroupId?: string;
  trelloCardId?: string;
  trelloListId?: string;
  listName?: string;
  boardId?: string;
  boardName?: string;
  archived?: boolean;
};

type TrelloList = {
  id: string;
  listId: string;
  listName: string;
  boardId: string;
  listPos: number;
};

type TrelloBoard = {
  id: string;
  boardId: string;
  boardName: string;
};

type TaskForm = {
  cardName: string;
  description: string;
  dueDate: string;
  assignees: string;
  calendarGroupId: string;
};

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
  location?: string;
  colorId?: string;
  attendees?: { email: string; displayName?: string }[];
  recurrence?: string[];
  start?: { dateTime?: string; date?: string };
  end?: { dateTime?: string; date?: string };
  calendarId: string;
  calendarSummary: string;
  calendarColor: string;
  statusKey: StatusKey;
};

type RepeatOption = "none" | "daily" | "weekly" | "monthly" | "yearly";

type EventForm = {
  summary: string;
  calendarId: string;
  allDay: boolean;
  date: string;
  startTime: string;
  endTime: string;
  description: string;
  location: string;
  repeat: RepeatOption;
  guests: string;
  colorId: string;
};

type ModalState =
  | { mode: "create"; date: string }
  | { mode: "edit"; event: CalendarEvent };

// ── ステータス定義 ────────────────────────────────────────
const STATUS_LABELS: Record<StatusKey, string> = { base: "参加", pen: "未決定", arc: "不参加" };
const STATUS_COLORS: Record<StatusKey, string> = { base: "#16a34a", pen: "#d97706", arc: "#6b7280" };
const STATUS_ICONS: Record<StatusKey, string>  = { base: "✓", pen: "?", arc: "✗" };

const EVENT_COLORS = [
  { id: "1",  color: "#7986CB", name: "ラベンダー" },
  { id: "2",  color: "#33B679", name: "セージ" },
  { id: "3",  color: "#8E24AA", name: "グレープ" },
  { id: "4",  color: "#E67C73", name: "フラミンゴ" },
  { id: "5",  color: "#F6BF26", name: "バナナ" },
  { id: "6",  color: "#F4511E", name: "タンジェリン" },
  { id: "7",  color: "#039BE5", name: "ピーコック" },
  { id: "8",  color: "#3F51B5", name: "ブルーベリー" },
  { id: "9",  color: "#0B8043", name: "バジル" },
  { id: "10", color: "#D50000", name: "トマト" },
  { id: "11", color: "#616161", name: "グラファイト" },
];
const RRULE_DAYS = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"];

// ── タスク API ───────────────────────────────────────────
async function fetchTasksFromAPI(groupId?: string): Promise<Task[]> {
  const url = groupId ? `/api/tasks?groupId=${encodeURIComponent(groupId)}` : "/api/tasks";
  const res = await fetch(url);
  if (!res.ok) return [];
  const rows = await res.json();
  return (rows ?? []).map((r: Record<string, unknown>) => ({
    id:              r.id as string,
    cardName:        (r.card_name ?? r.title) as string,
    description:     r.description as string | undefined,
    dueDate:         r.due_date as string | undefined,
    dueComplete:     r.due_complete as boolean | undefined,
    assignees:       (r.assignees ?? []) as string[],
    labels:          (r.labels ?? []) as TrelloLabel[],
    checklistTotal:  r.checklist_total as number | undefined,
    checklistDone:   r.checklist_done as number | undefined,
    checklists:      (r.checklists   ?? []) as Checklist[],
    membersData:     (r.members_data ?? []) as MemberData[],
    calendarGroupId: r.calendar_group_id as string | undefined,
    trelloCardId:    r.trello_card_id as string | undefined,
    trelloListId:    r.trello_list_id as string | undefined,
    listName:        r.list_name as string | undefined,
    boardId:         r.board_id as string | undefined,
    boardName:       r.board_name as string | undefined,
    archived:        r.archived as boolean | undefined,
  }));
}

async function createTaskAPI(t: Omit<Task, "id">): Promise<Task | null> {
  const res = await fetch("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      cardName:        t.cardName,
      description:     t.description || null,
      dueDate:         t.dueDate || null,
      calendarGroupId: t.calendarGroupId || null,
    }),
  });
  if (!res.ok) return null;
  const rows = await res.json();
  const r = Array.isArray(rows) ? rows[0] : rows;
  return r ? {
    id: r.id, cardName: r.card_name ?? r.title, description: r.description,
    dueDate: r.due_date, assignees: r.assignees ?? [], labels: r.labels ?? [],
    calendarGroupId: r.calendar_group_id, trelloCardId: r.trello_card_id,
    trelloListId: r.trello_list_id, listName: r.list_name,
    boardId: r.board_id, boardName: r.board_name,
  } : null;
}

async function deleteTaskAPI(id: string): Promise<void> {
  await fetch(`/api/tasks/${id}`, { method: "DELETE" });
}

async function fetchTrelloBoardsAndListsFromAPI(): Promise<{ boards: TrelloBoard[]; lists: TrelloList[] }> {
  const res = await fetch("/api/trello/boards");
  if (!res.ok) return { boards: [], lists: [] };
  const data = await res.json();
  const boards: TrelloBoard[] = (data.boards ?? []).map((r: Record<string, unknown>) => ({
    id:        r.id as string,
    boardId:   r.board_id as string,
    boardName: r.board_name as string,
  }));
  const lists: TrelloList[] = (data.lists ?? []).map((r: Record<string, unknown>) => ({
    id:       r.id as string,
    listId:   r.list_id as string,
    listName: r.list_name as string,
    boardId:  r.board_id as string,
    listPos:  (r.list_pos as number) ?? 0,
  }));
  return { boards, lists };
}

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
  if (!res.ok) {
    const err = await res.text().catch(() => "unknown");
    console.error("createGroupAPI failed:", res.status, err);
    return null;
  }
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
  if (form.location)    body.location    = form.location;
  if (form.colorId)     body.colorId     = form.colorId;
  if (form.guests.trim()) {
    body.attendees = form.guests.split(",")
      .map(e => e.trim()).filter(Boolean)
      .map(email => ({ email }));
  }
  if (form.repeat !== "none") {
    const day = RRULE_DAYS[new Date(`${form.date}T12:00:00`).getDay()];
    const rules: Record<string, string> = {
      daily:   "RRULE:FREQ=DAILY",
      weekly:  `RRULE:FREQ=WEEKLY;BYDAY=${day}`,
      monthly: "RRULE:FREQ=MONTHLY",
      yearly:  "RRULE:FREQ=YEARLY",
    };
    body.recurrence = [rules[form.repeat]];
  }
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

// ── タスクフォームモーダル ─────────────────────────────────────
// ── ボードビューコンポーネント ──────────────────────────────
function BoardView({ board, lists, tasks, draggingTaskId, dragOverListId, onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop, onDelete, onTaskClick, onListRename, onListCreate, onListReorder }: {
  board: TrelloBoard | null;
  lists: TrelloList[];
  tasks: Task[];
  draggingTaskId: string | null;
  dragOverListId: string | null;
  onDragStart: (taskId: string) => void;
  onDragEnd: () => void;
  onDragOver: (listId: string) => void;
  onDragLeave: () => void;
  onDrop: (taskId: string, list: TrelloList) => void;
  onDelete: (taskId: string) => void;
  onTaskClick: (taskId: string) => void;
  onListRename: (listId: string, newName: string) => void;
  onListCreate: (boardId: string, name: string) => void;
  onListReorder: (draggedListId: string, targetListId: string) => void;
}) {
  const [editingListId,   setEditingListId]   = useState<string | null>(null);
  const [editingListName, setEditingListName] = useState("");
  const [showNewList,     setShowNewList]     = useState(false);
  const [newListName,     setNewListName]     = useState("");
  const [draggingListId,  setDraggingListId]  = useState<string | null>(null);
  const [listDragOverId,  setListDragOverId]  = useState<string | null>(null);

  const commitListRename = (listId: string) => {
    const name = editingListName.trim();
    if (name) onListRename(listId, name);
    setEditingListId(null);
  };

  const commitListCreate = () => {
    const name = newListName.trim();
    if (name && board) { onListCreate(board.boardId, name); }
    setShowNewList(false);
    setNewListName("");
  };

  if (!board) return (
    <p className="text-sm text-gray-400 text-center py-12">
      ボードIDを入力して追加してください<br/>
      <span className="text-xs">（TrelloボードURL例: trello.com/b/<strong>AbCd1234</strong>/board-name）</span>
    </p>
  );
  const boardLists = lists
    .filter((l) => l.boardId === board.boardId)
    .sort((a, b) => a.listPos - b.listPos);
  return (
    <div className="flex gap-4 pb-2">
      {boardLists.map((list) => {
        const cardTasks = tasks.filter((t) => t.trelloListId === list.listId);
        const isTaskDragOver = dragOverListId === list.listId && !draggingListId;
        const isListDragOver = listDragOverId === list.listId && draggingListId && draggingListId !== list.listId;
        return (
          <div key={list.listId}
            className={`rounded-2xl shadow-sm border p-4 min-w-[220px] w-[220px] flex-shrink-0 transition-colors ${
              isListDragOver  ? "ring-2 ring-indigo-400 bg-indigo-50 border-indigo-300"
              : isTaskDragOver ? "bg-indigo-50 border-indigo-300"
              : draggingListId === list.listId ? "opacity-40 bg-white"
              : "bg-white"
            }`}
            onDragOver={(e) => {
              e.preventDefault();
              if (draggingListId) setListDragOverId(list.listId);
              else onDragOver(list.listId);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                if (draggingListId) setListDragOverId(null);
                else onDragLeave();
              }
            }}
            onDrop={(e) => {
              e.preventDefault();
              const lId = e.dataTransfer.getData("listId");
              const tId = e.dataTransfer.getData("taskId");
              if (lId && lId !== list.listId) {
                onListReorder(lId, list.listId);
                setDraggingListId(null);
                setListDragOverId(null);
              } else if (tId) {
                onDrop(tId, list);
              }
            }}>
            <div className="flex items-center gap-2 mb-3">
              {/* リスト並び替えドラッグハンドル */}
              <span
                className="text-gray-300 hover:text-gray-500 cursor-grab active:cursor-grabbing text-base flex-shrink-0 select-none"
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("listId", list.listId);
                  e.stopPropagation();
                  setDraggingListId(list.listId);
                }}
                onDragEnd={() => { setDraggingListId(null); setListDragOverId(null); }}
                title="ドラッグして並び替え"
              >⠿</span>
              {editingListId === list.listId ? (
                <input
                  className="flex-1 text-sm font-bold border-b-2 border-indigo-400 focus:outline-none bg-transparent py-0.5"
                  value={editingListName}
                  onChange={e => setEditingListName(e.target.value)}
                  onBlur={() => commitListRename(list.listId)}
                  onKeyDown={e => {
                    if (e.key === "Enter") { e.preventDefault(); commitListRename(list.listId); }
                    if (e.key === "Escape") setEditingListId(null);
                  }}
                  autoFocus
                />
              ) : (
                <h4
                  className="text-sm font-bold text-gray-700 truncate flex-1 cursor-pointer hover:text-indigo-600"
                  onClick={() => { setEditingListId(list.listId); setEditingListName(list.listName); }}
                  title="クリックして名前を変更"
                >
                  {list.listName}
                </h4>
              )}
              <span className="text-xs text-gray-400 flex-shrink-0">{cardTasks.length}</span>
            </div>
            <div className="space-y-2 min-h-[40px]">
              {cardTasks.map((task) => (
                <div key={task.id} draggable
                  onDragStart={(e) => { e.dataTransfer.setData("taskId", task.id); onDragStart(task.id); }}
                  onDragEnd={onDragEnd}
                  onClick={() => onTaskClick(task.id)}
                  className={`rounded-xl border border-gray-100 bg-white p-3 group cursor-pointer transition-opacity ${draggingTaskId === task.id ? "opacity-40" : "opacity-100"}`}>
                  <div className="flex items-start gap-1">
                    <p className="flex-1 text-sm font-medium text-gray-800 leading-snug">{task.cardName}</p>
                    <button onClick={(e) => { e.stopPropagation(); onDelete(task.id); }}
                      className="opacity-0 group-hover:opacity-100 transition-opacity text-gray-300 hover:text-red-500 text-xs ml-1 flex-shrink-0">✕</button>
                  </div>
                  {task.description && <p className="text-xs text-gray-400 mt-1 line-clamp-2">{task.description}</p>}
                  {task.labels && task.labels.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {task.labels.map((lb) => (
                        <span key={lb.id} className="text-[9px] rounded px-1.5 py-0.5 text-white font-medium"
                          style={{ backgroundColor: lb.color || "#aaa" }}>
                          {lb.name || lb.color}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center gap-2 mt-2 flex-wrap">
                    {task.dueDate && (
                      <span className={`text-[10px] ${task.dueComplete ? "line-through text-gray-300" : "text-gray-500"}`}>
                        📅 {task.dueDate}{task.dueComplete ? " ✓" : ""}
                      </span>
                    )}
                    {task.assignees && task.assignees.length > 0 && (
                      <span className="text-[10px] bg-gray-200 text-gray-600 rounded-full px-2 py-0.5">
                        👤 {task.assignees.join(", ")}
                      </span>
                    )}
                    {task.checklistTotal != null && task.checklistTotal > 0 && (
                      <span className={`text-[10px] rounded px-1.5 py-0.5 ${task.checklistDone === task.checklistTotal ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"}`}>
                        ☑ {task.checklistDone}/{task.checklistTotal}
                      </span>
                    )}
                  </div>
                </div>
              ))}
              {cardTasks.length === 0 && (
                <p className={`text-xs text-center py-4 ${isTaskDragOver ? "text-indigo-300" : "text-gray-300"}`}>
                  {isTaskDragOver ? "ここにドロップ" : "なし"}
                </p>
              )}
            </div>
          </div>
        );
      })}

      {/* + リストを追加 */}
      <div className="min-w-[220px] w-[220px] flex-shrink-0">
        {showNewList ? (
          <div className="rounded-2xl shadow-sm border p-4 bg-white flex flex-col gap-2">
            <input
              className="w-full border rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
              placeholder="リスト名"
              value={newListName}
              onChange={e => setNewListName(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") { e.preventDefault(); commitListCreate(); }
                if (e.key === "Escape") { setShowNewList(false); setNewListName(""); }
              }}
              autoFocus
            />
            <div className="flex gap-2">
              <button
                onClick={commitListCreate}
                className="flex-1 py-1 bg-indigo-600 text-white text-xs rounded hover:bg-indigo-700"
              >
                追加
              </button>
              <button
                onClick={() => { setShowNewList(false); setNewListName(""); }}
                className="px-2 py-1 text-gray-400 hover:text-gray-600 text-xs"
              >
                キャンセル
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setShowNewList(true)}
            className="w-full rounded-2xl border border-dashed border-gray-300 p-4 text-sm text-gray-400 hover:border-indigo-400 hover:text-indigo-500 transition-colors text-left"
          >
            + リストを追加
          </button>
        )}
      </div>
    </div>
  );
}

function TaskFormModal({
  onSave, onClose,
}: {
  onSave: (t: Omit<Task, "id">) => void;
  onClose: () => void;
}) {
  const [form, setForm] = useState<TaskForm>({
    cardName: "", description: "", dueDate: "", assignees: "", calendarGroupId: "",
  });
  const update = (p: Partial<TaskForm>) => setForm((prev) => ({ ...prev, ...p }));

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full sm:max-w-sm bg-white rounded-t-2xl sm:rounded-2xl shadow-2xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-bold">カードを追加</h3>
          <button onClick={onClose} className="w-7 h-7 rounded-full bg-gray-100 text-gray-500 hover:bg-gray-200 flex items-center justify-center">✕</button>
        </div>
        <input className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="カード名（必須）" value={form.cardName} onChange={(e) => update({ cardName: e.target.value })} autoFocus />
        <textarea className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm mb-3 resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="内容（任意）" rows={2} value={form.description} onChange={(e) => update({ description: e.target.value })} />
        <div className="flex gap-2 mb-3">
          <input type="date" className="flex-1 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={form.dueDate} onChange={(e) => update({ dueDate: e.target.value })} />
          <input className="flex-1 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="担当者" value={form.assignees} onChange={(e) => update({ assignees: e.target.value })} />
        </div>
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">キャンセル</button>
          <button onClick={() => {
            if (!form.cardName.trim()) return;
            onSave({ cardName: form.cardName.trim(), description: form.description || undefined,
              dueDate: form.dueDate || undefined,
              assignees: form.assignees ? form.assignees.split(",").map(s => s.trim()).filter(Boolean) : [] });
          }} disabled={!form.cardName.trim()}
            className="rounded-xl bg-indigo-600 px-4 py-2 text-sm text-white font-semibold hover:bg-indigo-700 disabled:opacity-50">追加</button>
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

  // タスク
  const [tasks, setTasks]               = useState<Task[]>([]);
  const [trelloBoards, setTrelloBoards]   = useState<TrelloBoard[]>([]);
  const [trelloLists, setTrelloLists]     = useState<TrelloList[]>([]);
  const [activeBoardId, setActiveBoardId] = useState<string | null>(null);
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [activeTab, setActiveTab]         = useState<"calendar" | "tasks">("calendar");
  const [draggingTaskId, setDraggingTaskId] = useState<string | null>(null);
  const [dragOverListId, setDragOverListId] = useState<string | null>(null);
  const [boardOrder, setBoardOrder]         = useState<string[]>([]);
  const [draggingBoardId, setDraggingBoardId] = useState<string | null>(null);
  const [boardDragOverId, setBoardDragOverId] = useState<string | null>(null);
  const [boardInput, setBoardInput]       = useState("");
  const [boardAdding, setBoardAdding]     = useState(false);
  const [boardAddError, setBoardAddError] = useState("");
  const [syncing, setSyncing]             = useState(false);
  const [showSettings, setShowSettings]   = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  const handleSync = async (boardId?: string) => {
    setSyncing(true);
    try {
      const url = boardId ? `/api/trello/sync?boardId=${boardId}` : "/api/trello/sync";
      await fetch(url, { method: "POST" });
      const updated = await fetchTasksFromAPI();
      setTasks(updated);
    } catch { /* ignore */ }
    finally { setSyncing(false); }
  };

  const handleAddBoard = async () => {
    const id = boardInput.trim();
    if (!id) return;
    setBoardAdding(true); setBoardAddError("");
    try {
      const res = await fetch("/api/trello/boards", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ boardId: id }),
      });
      if (!res.ok) { setBoardAddError("登録失敗"); return; }
      const { boards, lists } = await fetchTrelloBoardsAndListsFromAPI();
      setTrelloBoards(boards);
      setTrelloLists(lists);
      setActiveBoardId(boards.find(b => b.boardId === id)?.boardId ?? boards[0]?.boardId ?? null);
      setBoardInput("");
    } catch { setBoardAddError("エラーが発生しました"); }
    finally { setBoardAdding(false); }
  };

  // 初回ロード
  useEffect(() => {
    fetchGroupsFromAPI().then(setGroups);
    fetchTasksFromAPI().then(setTasks);
    fetchTrelloBoardsAndListsFromAPI().then(({ boards, lists }) => {
      setTrelloBoards(boards);
      setTrelloLists(lists);
      if (boards.length > 0) setActiveBoardId(boards[0].boardId);
    });
  }, []);

  // Supabase ↔ UI ポーリング（アクティブウィンドウ時のみ・30秒間隔）
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;

    const refresh = () => { fetchTasksFromAPI().then(setTasks); };

    const start = () => { if (!timer) timer = setInterval(refresh, 30_000); };
    const stop  = () => { if (timer) { clearInterval(timer); timer = null; } };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") { refresh(); start(); }
      else { stop(); }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    start();
    return () => { stop(); document.removeEventListener("visibilitychange", onVisibilityChange); };
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
          summary:     ev.summary     as string | undefined,
          description: ev.description as string | undefined,
          location:    ev.location    as string | undefined,
          colorId:     ev.colorId     as string | undefined,
          attendees:   ev.attendees   as { email: string; displayName?: string }[] | undefined,
          recurrence:  ev.recurrence  as string[] | undefined,
          start: ev.start as CalendarEvent["start"],
          end:   ev.end   as CalendarEvent["end"],
          calendarId:      calId,
          calendarSummary: info?.summary ?? calId,
          calendarColor:   info?.color ?? "#4285F4",
          statusKey:       info?.statusKey ?? "base",
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
    setForm({ summary: "", calendarId: primaryBaseId, allDay: false, date, startTime: "09:00", endTime: "10:00", description: "", location: "", repeat: "none", guests: "", colorId: "" });
    setSaveError(""); setConfirmDelete(false);
  };
  const openEdit = (ev: CalendarEvent) => {
    setModal({ mode: "edit", event: ev });
    setForm({
      summary:     ev.summary ?? "",
      calendarId:  ev.calendarId,
      allDay:      !ev.start?.dateTime,
      date:        ev.start?.date || ev.start?.dateTime?.slice(0, 10) || "",
      startTime:   ev.start?.dateTime ? formatTime(ev.start.dateTime) : "09:00",
      endTime:     ev.end?.dateTime   ? formatTime(ev.end.dateTime)   : "10:00",
      description: ev.description ?? "",
      location:    ev.location ?? "",
      repeat:      "none",
      guests:      (ev.attendees ?? []).map(a => a.email).join(", "),
      colorId:     ev.colorId ?? "",
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

  const changeStatus = async (ev: CalendarEvent, newStatus: StatusKey) => {
    if (ev.statusKey === newStatus) return;
    const triad = triads.find(t =>
      t.baseId === ev.calendarId || t.penId === ev.calendarId || t.arcId === ev.calendarId
    );
    if (!triad) return;
    const targetCalId =
      newStatus === "base" ? triad.baseId :
      newStatus === "pen"  ? triad.penId  :
                             triad.arcId;
    if (!targetCalId) { alert("対象カレンダーが見つかりません"); return; }

    // 楽観的 UI 更新
    const newColor = EVENT_COLORS.find(c => c.id === ev.colorId)?.color ?? STATUS_COLORS[newStatus];
    setEvents(p => p.map(e =>
      e.id === ev.id
        ? { ...e, calendarId: targetCalId, statusKey: newStatus, calendarColor: newColor }
        : e
    ));
    closeModal();

    await fetch("/api/calendar/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fromCalendarId: ev.calendarId, toCalendarId: targetCalId, eventId: ev.googleEventId }),
    });
    await fetchEvents();
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
      if (created) {
        setGroups((p) => [...p, created]);
      } else {
        alert("グループの保存に失敗しました。ブラウザのコンソールを確認してください。");
        return;
      }
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

  // ── タスク詳細モーダル用 ──────────────────────────────────
  const selectedTask = useMemo(
    () => tasks.find(t => t.id === selectedTaskId) ?? null,
    [tasks, selectedTaskId]
  );

  // ボード上のタスクから使用中のラベルを集約
  const availableLabels = useMemo(() => {
    const map = new Map<string, TrelloLabel>();
    for (const t of tasks) {
      if (!activeBoardId || t.boardId === activeBoardId) {
        for (const l of t.labels ?? []) { if (l.id) map.set(l.id, l); }
      }
    }
    return Array.from(map.values());
  }, [tasks, activeBoardId]);

  const handleTaskUpdate = useCallback((id: string, patch: Partial<Task>) => {
    setTasks(p => {
      const updated = p.map(t => t.id === id ? { ...t, ...patch } : t);
      return patch.archived ? updated.filter(t => !t.archived) : updated;
    });
  }, []);

  // ── タスク操作 ─────────────────────────────────────────
  const handleTaskCreate = async (t: Omit<Task, "id">) => {
    const created = await createTaskAPI(t);
    if (created) setTasks((p) => [...p, created]);
    setShowTaskModal(false);
  };

  const handleTaskListChange = async (id: string, listId: string, listName: string, boardId: string, boardName: string) => {
    const task = tasks.find(t => t.id === id);
    setTasks((p) => p.map((t) => t.id === id ? { ...t, trelloListId: listId, listName, boardId, boardName } : t));
    await fetch(`/api/tasks/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trelloListId: listId, listName, boardId, boardName, trelloCardId: task?.trelloCardId }),
    });
  };

  const handleTaskDelete = async (id: string) => {
    setTasks((p) => p.filter((t) => t.id !== id));
    await deleteTaskAPI(id);
  };

  const handleListRename = async (listId: string, newName: string) => {
    setTrelloLists(p => p.map(l => l.listId === listId ? { ...l, listName: newName } : l));
    await fetch("/api/trello/lists", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ listId, name: newName }),
    });
  };

  const handleListCreate = async (boardId: string, name: string) => {
    const res = await fetch("/api/trello/lists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ boardId, name }),
    });
    if (res.ok) {
      const data = await res.json();
      setTrelloLists(p => [...p, { id: data.id, listId: data.listId, listName: data.listName, boardId, listPos: data.listPos ?? 0 }]);
    }
  };

  // ── ボード並び替え（localStorage で永続化）────────────────
  // ボード読み込み時に localStorage の保存順を適用
  useEffect(() => {
    if (trelloBoards.length === 0) return;
    const saved: string[] = JSON.parse(localStorage.getItem("boardOrder") ?? "[]");
    const existingIds = new Set(trelloBoards.map(b => b.boardId));
    const merged = [
      ...saved.filter(id => existingIds.has(id)),
      ...trelloBoards.map(b => b.boardId).filter(id => !saved.includes(id)),
    ];
    setBoardOrder(merged);
  }, [trelloBoards]);

  const sortedBoards = useMemo(() => {
    if (boardOrder.length === 0) return trelloBoards;
    const map = new Map(trelloBoards.map(b => [b.boardId, b]));
    return boardOrder.map(id => map.get(id)).filter(Boolean) as TrelloBoard[];
  }, [trelloBoards, boardOrder]);

  const handleBoardReorder = (draggedId: string, targetId: string) => {
    setBoardOrder(prev => {
      const current = prev.length > 0 ? prev : trelloBoards.map(b => b.boardId);
      const dragIdx   = current.indexOf(draggedId);
      const targetIdx = current.indexOf(targetId);
      if (dragIdx === -1 || targetIdx === -1 || dragIdx === targetIdx) return prev;
      const next = [...current];
      next.splice(dragIdx, 1);
      next.splice(targetIdx, 0, draggedId);
      localStorage.setItem("boardOrder", JSON.stringify(next));
      return next;
    });
  };

  const handleListReorder = async (draggedListId: string, targetListId: string) => {
    if (!activeBoardId || draggedListId === targetListId) return;
    const boardLists = trelloLists
      .filter(l => l.boardId === activeBoardId)
      .sort((a, b) => a.listPos - b.listPos);
    const dragIdx   = boardLists.findIndex(l => l.listId === draggedListId);
    const targetIdx = boardLists.findIndex(l => l.listId === targetListId);
    if (dragIdx === -1 || targetIdx === -1) return;

    // 並び替え後の配列
    const reordered = [...boardLists];
    const [dragged] = reordered.splice(dragIdx, 1);
    reordered.splice(targetIdx, 0, dragged);

    // 挿入位置の前後から新しい pos を計算
    const insertIdx = reordered.findIndex(l => l.listId === draggedListId);
    const prev = reordered[insertIdx - 1];
    const next = reordered[insertIdx + 1];
    let newPos: number;
    if (!prev)       newPos = (next?.listPos ?? 16384) / 2;
    else if (!next)  newPos = prev.listPos + 16384;
    else             newPos = (prev.listPos + next.listPos) / 2;

    // 楽観的 UI 更新
    setTrelloLists(p => p.map(l => l.listId === draggedListId ? { ...l, listPos: newPos } : l));

    // Trello + Supabase に反映
    await fetch("/api/trello/lists", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ listId: draggedListId, pos: newPos }),
    });
  };

  // ── メイン ───────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b px-4 py-3 flex items-center justify-between sticky top-0 z-10 shadow-sm">
        <h1 className="text-lg font-bold text-gray-800">📅 My Calendar</h1>
        <div className="flex items-center gap-2">
          <div className="flex rounded-xl border border-gray-200 overflow-hidden text-sm">
            <button onClick={() => setActiveTab("calendar")}
              className={`px-4 py-1.5 font-medium transition-colors ${activeTab === "calendar" ? "bg-blue-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}>
              カレンダー
            </button>
            <button onClick={() => setActiveTab("tasks")}
              className={`px-4 py-1.5 font-medium transition-colors ${activeTab === "tasks" ? "bg-indigo-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}>
              タスク {tasks.length > 0 && (
                <span className="ml-1 bg-indigo-100 text-indigo-700 rounded-full px-1.5 text-xs">{tasks.length}</span>
              )}
            </button>
          </div>
          <button onClick={() => setShowSettings(true)}
            className="rounded-xl border border-gray-200 px-3 py-1.5 text-sm text-gray-500 hover:bg-gray-50 transition-colors">
            ⚙️
          </button>
        </div>
      </header>

      {/* ── ボードビュー ── */}
      {activeTab === "tasks" && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* ボードタブ行 */}
          <div className="bg-white border-b px-4 py-2 flex items-center gap-2 overflow-x-auto">
            {sortedBoards.map((board) => (
              <button
                key={board.boardId}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("boardId", board.boardId);
                  setDraggingBoardId(board.boardId);
                }}
                onDragEnd={() => { setDraggingBoardId(null); setBoardDragOverId(null); }}
                onDragOver={(e) => { e.preventDefault(); setBoardDragOverId(board.boardId); }}
                onDragLeave={() => setBoardDragOverId(null)}
                onDrop={(e) => {
                  e.preventDefault();
                  const draggedId = e.dataTransfer.getData("boardId");
                  if (draggedId && draggedId !== board.boardId) handleBoardReorder(draggedId, board.boardId);
                  setDraggingBoardId(null); setBoardDragOverId(null);
                }}
                onClick={() => setActiveBoardId(board.boardId)}
                className={`flex-shrink-0 px-4 py-1.5 rounded-lg text-sm font-medium transition-all select-none cursor-grab active:cursor-grabbing ${
                  draggingBoardId === board.boardId ? "opacity-40" :
                  boardDragOverId === board.boardId && draggingBoardId !== board.boardId ? "ring-2 ring-indigo-400" : ""
                } ${activeBoardId === board.boardId ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>
                {board.boardName}
              </button>
            ))}
            <div className="flex items-center gap-1 ml-2 flex-shrink-0">
              <input value={boardInput} onChange={(e) => setBoardInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAddBoard()}
                placeholder="ボードID" className="w-32 rounded-lg border border-gray-200 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400" />
              <button onClick={handleAddBoard} disabled={boardAdding || !boardInput.trim()}
                className="rounded-lg bg-gray-100 px-2 py-1 text-xs text-gray-600 hover:bg-gray-200 disabled:opacity-50">
                {boardAdding ? "…" : "+ 追加"}
              </button>
              {boardAddError && <span className="text-xs text-red-500">{boardAddError}</span>}
            </div>
            <button onClick={() => handleSync(activeBoardId ?? undefined)} disabled={syncing || !activeBoardId}
              className="flex-shrink-0 ml-2 rounded-lg bg-green-100 px-3 py-1.5 text-xs text-green-700 font-medium hover:bg-green-200 disabled:opacity-50">
              {syncing ? "同期中…" : "↻ 同期"}
            </button>
          </div>

          {/* 選択中ボードのリスト・カード */}
          <div className="flex-1 overflow-x-auto p-4">
            <BoardView
              board={trelloBoards.find(b => b.boardId === activeBoardId) ?? null}
              lists={trelloLists}
              tasks={tasks}
              draggingTaskId={draggingTaskId}
              dragOverListId={dragOverListId}
              onDragStart={(taskId) => setDraggingTaskId(taskId)}
              onDragEnd={() => { setDraggingTaskId(null); setDragOverListId(null); }}
              onDragOver={(listId) => setDragOverListId(listId)}
              onDragLeave={() => setDragOverListId(null)}
              onDrop={(taskId, list) => {
                handleTaskListChange(taskId, list.listId, list.listName,
                  trelloBoards.find(b => b.boardId === activeBoardId)?.boardId ?? "",
                  trelloBoards.find(b => b.boardId === activeBoardId)?.boardName ?? "");
                setDragOverListId(null); setDraggingTaskId(null);
              }}
              onDelete={handleTaskDelete}
              onTaskClick={(taskId) => setSelectedTaskId(taskId)}
              onListRename={handleListRename}
              onListCreate={handleListCreate}
              onListReorder={handleListReorder}
            />
          </div>
        </div>
      )}

      {/* タスク詳細モーダル */}
      {selectedTask && (
        <TaskDetailModal
          task={selectedTask}
          availableLabels={availableLabels}
          onUpdate={handleTaskUpdate}
          onDelete={(id) => setTasks(p => p.filter(t => t.id !== id))}
          onClose={() => setSelectedTaskId(null)}
        />
      )}

      {/* ステータスボタン + カレンダー本体 */}
      {activeTab === "calendar" && (<>
      <div className="bg-white border-b px-4 py-3">
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
                  {ev.location && <p className="text-xs text-gray-400 mt-0.5">📍 {ev.location}</p>}
                  {ev.description && <p className="text-xs text-gray-400 mt-1 line-clamp-2">{ev.description}</p>}
                  <div className="flex gap-1 mt-2">
                    {(["base", "pen", "arc"] as StatusKey[]).map(s => {
                      const active = ev.statusKey === s;
                      return (
                        <button key={s}
                          onClick={e => { e.stopPropagation(); changeStatus(ev, s); }}
                          className={`flex-1 py-0.5 text-[10px] font-semibold rounded-lg border transition-all ${
                            active ? "text-white border-transparent" : "text-gray-400 border-gray-200 hover:border-gray-300"
                          }`}
                          style={active ? { backgroundColor: STATUS_COLORS[s] } : {}}>
                          {STATUS_ICONS[s]} {STATUS_LABELS[s]}
                        </button>
                      );
                    })}
                  </div>
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

            {/* ステータス切り替え */}
            {modal.mode === "edit" && (() => {
              const ev = (modal as { mode: "edit"; event: CalendarEvent }).event;
              return (
                <div className="flex gap-2 mb-4">
                  {(["base", "pen", "arc"] as StatusKey[]).map(s => {
                    const active = ev.statusKey === s;
                    return (
                      <button key={s}
                        onClick={() => changeStatus(ev, s)}
                        className={`flex-1 py-2 rounded-xl text-sm font-semibold border transition-all ${
                          active ? "text-white border-transparent" : "bg-white text-gray-500 border-gray-200 hover:border-gray-300"
                        }`}
                        style={active ? { backgroundColor: STATUS_COLORS[s] } : {}}>
                        {STATUS_ICONS[s]} {STATUS_LABELS[s]}
                      </button>
                    );
                  })}
                </div>
              );
            })()}

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
            <div className="mb-3">
              <textarea placeholder="内容（任意）" value={form.description} rows={3}
                onChange={(e) => updateForm({ description: e.target.value })}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none" />
            </div>

            {/* 場所 */}
            <div className="mb-3">
              <input type="text" placeholder="📍 場所（任意）" value={form.location}
                onChange={(e) => updateForm({ location: e.target.value })}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>

            {/* 繰り返し */}
            <div className="mb-3">
              <select value={form.repeat}
                onChange={(e) => updateForm({ repeat: e.target.value as RepeatOption })}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                <option value="none">🔁 繰り返しなし</option>
                <option value="daily">毎日</option>
                <option value="weekly">毎週</option>
                <option value="monthly">毎月</option>
                <option value="yearly">毎年</option>
              </select>
            </div>

            {/* ゲスト */}
            <div className="mb-3">
              <input type="text" placeholder="👥 ゲスト（メール、カンマ区切り）" value={form.guests}
                onChange={(e) => updateForm({ guests: e.target.value })}
                className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>

            {/* カラー */}
            <div className="mb-4">
              <div className="flex gap-1.5 flex-wrap">
                <button
                  onClick={() => updateForm({ colorId: "" })}
                  className={`w-6 h-6 rounded-full border-2 bg-gray-200 ${form.colorId === "" ? "border-gray-500" : "border-transparent"}`}
                  title="カレンダーの色を使用" />
                {EVENT_COLORS.map(c => (
                  <button key={c.id}
                    onClick={() => updateForm({ colorId: c.id })}
                    className={`w-6 h-6 rounded-full border-2 ${form.colorId === c.id ? "border-gray-500 scale-110" : "border-transparent"} transition-transform`}
                    style={{ backgroundColor: c.color }}
                    title={c.name} />
                ))}
              </div>
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

      </>)}

      {/* グループ管理モーダル */}
      {showGroupModal && (
        <GroupModal triads={triads} editGroup={editingGroup}
          onSave={handleGroupSave}
          onClose={() => { setShowGroupModal(false); setEditingGroup(null); }} />
      )}

      {/* タスク追加モーダル */}
      {showTaskModal && (
        <TaskFormModal onSave={handleTaskCreate}
          onClose={() => setShowTaskModal(false)} />
      )}

      {/* 設定パネル */}
      {showSettings && (
        <div className="fixed inset-0 z-50 flex">
          {/* オーバーレイ */}
          <div className="flex-1 bg-black/40" onClick={() => setShowSettings(false)} />
          {/* パネル本体 */}
          <div className="w-80 bg-white h-full shadow-2xl flex flex-col overflow-y-auto">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <h2 className="font-bold text-gray-800">⚙️ 設定</h2>
              <button onClick={() => setShowSettings(false)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
            </div>

            <div className="px-4 py-4 space-y-4">
              <h3 className="text-sm font-semibold text-gray-600">カレンダー選択</h3>
              {calLoading ? (
                <p className="text-xs text-gray-400">読み込み中...</p>
              ) : (
                <div className="flex flex-wrap gap-2">
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
                </div>
              )}

              <h3 className="text-sm font-semibold text-gray-600 pt-2">グループ</h3>
              <div className="flex flex-wrap gap-2">
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
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
