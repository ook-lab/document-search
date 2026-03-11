"use client";
import { useState, useEffect, useRef } from "react";

export type TrelloLabel = { id: string; name: string; color: string };

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
  const titleRef = useRef<HTMLInputElement>(null);

  // タスクが切り替わったらフォームをリセット
  useEffect(() => {
    setTitle(task.cardName);
    setDescription(task.description ?? "");
    setDueDate(task.dueDate ?? "");
    setDueComplete(task.dueComplete ?? false);
    setLabels(task.labels ?? []);
  }, [task.id]);

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

  const checklistPct = task.checklistTotal
    ? Math.round(((task.checklistDone ?? 0) / task.checklistTotal) * 100)
    : 0;

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

          {/* 担当者（表示のみ・変更はTrello側で） */}
          {(task.assignees ?? []).length > 0 && (
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">担当者</label>
              <div className="flex flex-wrap gap-2">
                {(task.assignees ?? []).map((a, i) => (
                  <span key={i} className="bg-gray-100 text-gray-700 rounded-full px-3 py-1 text-sm">{a}</span>
                ))}
              </div>
              <p className="text-xs text-gray-400 mt-1">担当者の変更はTrelloで行ってください</p>
            </div>
          )}

          {/* チェックリスト（表示のみ） */}
          {(task.checklistTotal ?? 0) > 0 && (
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                チェックリスト {task.checklistDone}/{task.checklistTotal} ({checklistPct}%)
              </label>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className="h-2 rounded-full transition-all"
                  style={{
                    width: `${checklistPct}%`,
                    backgroundColor: checklistPct === 100 ? "#22c55e" : "#6366f1",
                  }}
                />
              </div>
              <p className="text-xs text-gray-400 mt-1">チェックリストの編集はTrelloで行ってください</p>
            </div>
          )}
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
