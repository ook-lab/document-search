"use client";
import { useState, useEffect, useRef } from "react";

export type TrelloLabel = { id: string; name: string; color: string };
export type CheckItem   = { id: string; name: string; state: "complete" | "incomplete" };
export type Checklist   = { id: string; name: string; checkItems: CheckItem[] };
export type MemberData  = { id: string; name: string };
export type BoardMember = { id: string; fullName: string; username: string };

export type Task = {
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

type Props = {
  task: Task;
  availableLabels: TrelloLabel[];
  onUpdate: (id: string, patch: Partial<Task>) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
};

// Trelloカラー名 → hex
const TRELLO_COLORS: Record<string, string> = {
  green: "#4bce97", yellow: "#f5cd47", orange: "#fea362", red: "#f87168",
  purple: "#9f8fef", blue: "#579dff", sky: "#6cc3e0", lime: "#94c748",
  pink: "#e774bb", black: "#8590a2",
  green_dark: "#1f845a", yellow_dark: "#e2b203", orange_dark: "#c25100",
  red_dark: "#ae2e24", purple_dark: "#6e5dc6", blue_dark: "#0055cc",
  sky_dark: "#206a83", lime_dark: "#4c6b1f", pink_dark: "#943d73",
  black_dark: "#626f86",
};
function labelBg(color: string) { return TRELLO_COLORS[color] ?? color ?? "#b3bac5"; }

export default function TaskDetailModal({ task, availableLabels, onUpdate, onDelete, onClose }: Props) {
  const [title,       setTitle]       = useState(task.cardName);
  const [description, setDescription] = useState(task.description ?? "");
  const [dueDate,     setDueDate]     = useState(task.dueDate ?? "");
  const [dueComplete, setDueComplete] = useState(task.dueComplete ?? false);
  const [labels,      setLabels]      = useState<TrelloLabel[]>(task.labels ?? []);
  const [saving,      setSaving]      = useState(false);

  const [checklists,        setChecklists]        = useState<Checklist[]>(task.checklists ?? []);
  const [membersData,       setMembersData]       = useState<MemberData[]>(task.membersData ?? []);
  const [boardMembers,      setBoardMembers]      = useState<BoardMember[]>([]);
  const [newChecklistName,  setNewChecklistName]  = useState("");
  const [showAddChecklist,  setShowAddChecklist]  = useState(false);
  const [newItemNames,      setNewItemNames]      = useState<Record<string, string>>({});
  const [showAddItem,       setShowAddItem]       = useState<Record<string, boolean>>({});

  const titleRef = useRef<HTMLInputElement>(null);

  // タスクが切り替わったらフォームをリセット
  useEffect(() => {
    setTitle(task.cardName);
    setDescription(task.description ?? "");
    setDueDate(task.dueDate ?? "");
    setDueComplete(task.dueComplete ?? false);
    setLabels(task.labels ?? []);
    setChecklists(task.checklists ?? []);
    setMembersData(task.membersData ?? []);
    setNewChecklistName("");
    setShowAddChecklist(false);
    setNewItemNames({});
    setShowAddItem({});
  }, [task.id]);

  // ボードメンバーのfetch
  useEffect(() => {
    if (!task.boardId) return;
    fetch(`/api/trello/board-members?boardId=${task.boardId}`)
      .then(r => r.json())
      .then(setBoardMembers)
      .catch(() => {});
  }, [task.boardId]);

  const save = async (patch: Partial<Task>) => {
    setSaving(true);
    try {
      await fetch(`/api/tasks/${task.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...patch, trelloCardId: task.trelloCardId }),
      });
      onUpdate(task.id, patch);
    } finally {
      setSaving(false);
    }
  };

  const handleTitleBlur = () => {
    if (title.trim() && title !== task.cardName) save({ cardName: title.trim() });
  };

  const handleDescBlur = () => {
    if (description !== (task.description ?? "")) save({ description: description || undefined });
  };

  const handleDueDateChange = (val: string) => {
    setDueDate(val);
    save({ dueDate: val || undefined });
  };

  const handleDueCompleteChange = (val: boolean) => {
    setDueComplete(val);
    save({ dueComplete: val });
  };

  const handleLabelToggle = (label: TrelloLabel) => {
    const has = labels.some(l => l.id === label.id);
    const next = has ? labels.filter(l => l.id !== label.id) : [...labels, label];
    setLabels(next);
    save({ labels: next });
  };

  const handleArchive = async () => {
    await save({ archived: true });
    onClose();
  };

  const handleDelete = async () => {
    if (!confirm("このタスクを削除しますか？\nTrelloカードは削除されません（アーカイブのみ）。")) return;
    await fetch(`/api/tasks/${task.id}`, { method: "DELETE" });
    onDelete(task.id);
    onClose();
  };

  // ── チェックリスト操作 ──────────────────────────────────

  const handleItemToggle = async (checklistId: string, item: CheckItem) => {
    const newState = item.state === "complete" ? "incomplete" : "complete";
    // optimistic update
    setChecklists(prev => prev.map(cl =>
      cl.id !== checklistId ? cl : {
        ...cl,
        checkItems: cl.checkItems.map(i => i.id === item.id ? { ...i, state: newState } : i),
      }
    ));
    const res = await fetch(`/api/tasks/${task.id}/checklist-item`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ checklistId, checkItemId: item.id, state: newState, trelloCardId: task.trelloCardId }),
    });
    if (res.ok) {
      const data = await res.json();
      onUpdate(task.id, { checklistTotal: data.checklist_total, checklistDone: data.checklist_done });
    }
  };

  const handleAddItem = async (checklistId: string) => {
    const name = newItemNames[checklistId]?.trim();
    if (!name) return;
    setNewItemNames(p => ({ ...p, [checklistId]: "" }));
    const res = await fetch(`/api/tasks/${task.id}/checklist-item`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ checklistId, name, trelloCardId: task.trelloCardId }),
    });
    if (res.ok) {
      const newItem: CheckItem = await res.json();
      setChecklists(prev => prev.map(cl =>
        cl.id !== checklistId ? cl : { ...cl, checkItems: [...cl.checkItems, newItem] }
      ));
    }
  };

  const handleAddChecklist = async () => {
    const name = newChecklistName.trim();
    if (!name || !task.trelloCardId) return;
    setNewChecklistName("");
    setShowAddChecklist(false);
    const res = await fetch(`/api/tasks/${task.id}/checklist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, trelloCardId: task.trelloCardId }),
    });
    if (res.ok) {
      const newChecklist: Checklist = await res.json();
      setChecklists(prev => [...prev, newChecklist]);
    }
  };

  const handleDeleteChecklist = async (checklistId: string) => {
    setChecklists(prev => prev.filter(cl => cl.id !== checklistId));
    await fetch(`/api/tasks/${task.id}/checklist?checklistId=${checklistId}`, { method: "DELETE" });
  };

  const handleDeleteItem = async (checklistId: string, checkItemId: string) => {
    setChecklists(prev => prev.map(cl =>
      cl.id !== checklistId ? cl : { ...cl, checkItems: cl.checkItems.filter(i => i.id !== checkItemId) }
    ));
    const params = new URLSearchParams({ checklistId, checkItemId, trelloCardId: task.trelloCardId ?? "" });
    await fetch(`/api/tasks/${task.id}/checklist-item?${params}`, { method: "DELETE" });
  };

  // ── メンバー操作 ────────────────────────────────────────

  const handleMemberToggle = async (member: BoardMember) => {
    const isAssigned = membersData.some(m => m.id === member.id);
    if (isAssigned) {
      // 削除
      setMembersData(prev => prev.filter(m => m.id !== member.id));
      const params = new URLSearchParams({ memberId: member.id, trelloCardId: task.trelloCardId ?? "" });
      await fetch(`/api/tasks/${task.id}/members?${params}`, { method: "DELETE" });
      onUpdate(task.id, {
        assignees: membersData.filter(m => m.id !== member.id).map(m => m.name),
        membersData: membersData.filter(m => m.id !== member.id),
      });
    } else {
      // 追加
      const newMember: MemberData = { id: member.id, name: member.fullName };
      setMembersData(prev => [...prev, newMember]);
      await fetch(`/api/tasks/${task.id}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ memberId: member.id, memberName: member.fullName, trelloCardId: task.trelloCardId }),
      });
      onUpdate(task.id, {
        assignees: [...membersData, newMember].map(m => m.name),
        membersData: [...membersData, newMember],
      });
    }
  };

  // チェックリスト進捗計算
  const totalItems = checklists.reduce((sum, cl) => sum + (cl.checkItems?.length ?? 0), 0);
  const doneItems  = checklists.reduce((sum, cl) => sum + (cl.checkItems?.filter(i => i.state === "complete").length ?? 0), 0);
  const checklistPct = totalItems > 0 ? Math.round((doneItems / totalItems) * 100) : 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div
        className="relative w-full max-w-lg bg-white h-full overflow-y-auto shadow-2xl flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* ヘッダー */}
        <div className="flex items-start gap-2 px-6 pt-6 pb-4 border-b">
          <input
            ref={titleRef}
            className="flex-1 text-lg font-semibold border-b-2 border-transparent hover:border-gray-200 focus:border-blue-400 focus:outline-none py-1 bg-transparent"
            value={title}
            onChange={e => setTitle(e.target.value)}
            onBlur={handleTitleBlur}
          />
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl mt-1">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-5">
          {/* ボード / リスト */}
          {(task.boardName || task.listName) && (
            <p className="text-xs text-gray-400">
              {task.boardName}{task.listName && <span> › {task.listName}</span>}
            </p>
          )}

          {/* 説明 */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">説明</label>
            <textarea
              className="w-full border rounded-lg p-3 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-300 min-h-[100px]"
              placeholder="説明を追加..."
              value={description}
              onChange={e => setDescription(e.target.value)}
              onBlur={handleDescBlur}
            />
          </div>

          {/* 期日 */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">期日</label>
            <div className="flex items-center gap-3 flex-wrap">
              <input
                type="date"
                className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
                value={dueDate}
                onChange={e => handleDueDateChange(e.target.value)}
              />
              {dueDate && (
                <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={dueComplete}
                    onChange={e => handleDueCompleteChange(e.target.checked)}
                    className="w-4 h-4 accent-green-500"
                  />
                  完了
                </label>
              )}
              {dueDate && (
                <button className="text-xs text-gray-400 hover:text-red-500" onClick={() => handleDueDateChange("")}>
                  削除
                </button>
              )}
            </div>
          </div>

          {/* ラベル */}
          {availableLabels.length > 0 && (
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">ラベル</label>
              <div className="flex flex-wrap gap-2">
                {availableLabels.map(label => {
                  const active = labels.some(l => l.id === label.id);
                  return (
                    <button
                      key={label.id}
                      onClick={() => handleLabelToggle(label)}
                      className="flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium text-white transition-opacity"
                      style={{
                        backgroundColor: labelBg(label.color),
                        opacity: active ? 1 : 0.35,
                      }}
                    >
                      {active && <span>✓</span>}
                      {label.name || label.color}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* メンバー */}
          {boardMembers.length > 0 && (
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">メンバー</label>
              <div className="flex flex-wrap gap-2">
                {boardMembers.map(member => {
                  const assigned = membersData.some(m => m.id === member.id);
                  return (
                    <button
                      key={member.id}
                      onClick={() => handleMemberToggle(member)}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-all"
                      style={{
                        opacity: assigned ? 1 : 0.4,
                        backgroundColor: assigned ? "#dbeafe" : "#f3f4f6",
                        borderColor: assigned ? "#93c5fd" : "#e5e7eb",
                        color: assigned ? "#1d4ed8" : "#374151",
                      }}
                    >
                      {assigned && <span>✓</span>}
                      {member.fullName}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* チェックリスト */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              チェックリスト
              {totalItems > 0 && (
                <span className="ml-2 font-normal normal-case text-gray-400">
                  {doneItems}/{totalItems} ({checklistPct}%)
                </span>
              )}
            </label>

            {/* 全体進捗バー */}
            {totalItems > 0 && (
              <div className="w-full bg-gray-200 rounded-full h-1.5 mb-3">
                <div
                  className="h-1.5 rounded-full transition-all"
                  style={{
                    width: `${checklistPct}%`,
                    backgroundColor: checklistPct === 100 ? "#22c55e" : "#6366f1",
                  }}
                />
              </div>
            )}

            {/* 各チェックリスト */}
            <div className="flex flex-col gap-4">
              {checklists.map(cl => {
                const clTotal = cl.checkItems?.length ?? 0;
                const clDone  = cl.checkItems?.filter(i => i.state === "complete").length ?? 0;
                const clPct   = clTotal > 0 ? Math.round((clDone / clTotal) * 100) : 0;
                return (
                  <div key={cl.id} className="border rounded-lg p-3">
                    {/* チェックリストヘッダー */}
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-semibold text-gray-700">{cl.name}</span>
                      <div className="flex items-center gap-2">
                        {clTotal > 0 && (
                          <span className="text-xs text-gray-400">{clDone}/{clTotal}</span>
                        )}
                        <button
                          onClick={() => handleDeleteChecklist(cl.id)}
                          className="text-gray-300 hover:text-red-500 text-xs transition-colors"
                          title="チェックリストを削除"
                        >
                          ✕
                        </button>
                      </div>
                    </div>

                    {/* 進捗バー */}
                    {clTotal > 0 && (
                      <div className="w-full bg-gray-100 rounded-full h-1 mb-2">
                        <div
                          className="h-1 rounded-full transition-all"
                          style={{
                            width: `${clPct}%`,
                            backgroundColor: clPct === 100 ? "#22c55e" : "#6366f1",
                          }}
                        />
                      </div>
                    )}

                    {/* アイテム一覧 */}
                    <div className="flex flex-col gap-1">
                      {(cl.checkItems ?? []).map(item => (
                        <div key={item.id} className="flex items-center gap-2 group py-0.5">
                          <input
                            type="checkbox"
                            checked={item.state === "complete"}
                            onChange={() => handleItemToggle(cl.id, item)}
                            className="w-4 h-4 accent-indigo-500 flex-shrink-0 cursor-pointer"
                          />
                          <span className={`flex-1 text-sm ${item.state === "complete" ? "line-through text-gray-400" : "text-gray-700"}`}>
                            {item.name}
                          </span>
                          <button
                            onClick={() => handleDeleteItem(cl.id, item.id)}
                            className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-500 text-xs transition-all flex-shrink-0"
                            title="アイテムを削除"
                          >
                            ✕
                          </button>
                        </div>
                      ))}
                    </div>

                    {/* アイテム追加 */}
                    {showAddItem[cl.id] ? (
                      <div className="mt-2 flex gap-2">
                        <input
                          className="flex-1 border rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
                          placeholder="アイテム名"
                          value={newItemNames[cl.id] ?? ""}
                          onChange={e => setNewItemNames(p => ({ ...p, [cl.id]: e.target.value }))}
                          onKeyDown={e => {
                            if (e.key === "Enter") { e.preventDefault(); handleAddItem(cl.id); }
                            if (e.key === "Escape") setShowAddItem(p => ({ ...p, [cl.id]: false }));
                          }}
                          autoFocus
                        />
                        <button
                          onClick={() => handleAddItem(cl.id)}
                          className="px-2 py-1 bg-indigo-600 text-white text-xs rounded hover:bg-indigo-700"
                        >
                          追加
                        </button>
                        <button
                          onClick={() => setShowAddItem(p => ({ ...p, [cl.id]: false }))}
                          className="px-2 py-1 text-gray-400 hover:text-gray-600 text-xs"
                        >
                          キャンセル
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setShowAddItem(p => ({ ...p, [cl.id]: true }))}
                        className="mt-2 text-xs text-indigo-500 hover:text-indigo-700 hover:underline"
                      >
                        + アイテムを追加
                      </button>
                    )}
                  </div>
                );
              })}
            </div>

            {/* チェックリスト追加 */}
            {task.trelloCardId && (
              showAddChecklist ? (
                <div className="mt-3 flex gap-2">
                  <input
                    className="flex-1 border rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
                    placeholder="チェックリスト名"
                    value={newChecklistName}
                    onChange={e => setNewChecklistName(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === "Enter") { e.preventDefault(); handleAddChecklist(); }
                      if (e.key === "Escape") { setShowAddChecklist(false); setNewChecklistName(""); }
                    }}
                    autoFocus
                  />
                  <button
                    onClick={handleAddChecklist}
                    className="px-2 py-1 bg-indigo-600 text-white text-xs rounded hover:bg-indigo-700"
                  >
                    追加
                  </button>
                  <button
                    onClick={() => { setShowAddChecklist(false); setNewChecklistName(""); }}
                    className="px-2 py-1 text-gray-400 hover:text-gray-600 text-xs"
                  >
                    キャンセル
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setShowAddChecklist(true)}
                  className="mt-3 text-xs text-indigo-500 hover:text-indigo-700 hover:underline"
                >
                  + チェックリストを追加
                </button>
              )
            )}
          </div>
        </div>

        {/* フッター：アクション */}
        <div className="px-6 py-4 border-t flex flex-col gap-2">
          {saving && <p className="text-xs text-blue-500 text-right">保存中...</p>}
          <button
            onClick={handleArchive}
            className="w-full py-2 px-4 rounded-lg bg-yellow-50 hover:bg-yellow-100 text-yellow-800 text-sm font-medium text-left transition-colors"
          >
            📦 アーカイブ（Trelloにも反映）
          </button>
          <button
            onClick={handleDelete}
            className="w-full py-2 px-4 rounded-lg bg-red-50 hover:bg-red-100 text-red-700 text-sm font-medium text-left transition-colors"
          >
            🗑 このアプリから削除（Trelloには残る）
          </button>
        </div>
      </div>
    </div>
  );
}
