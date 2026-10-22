from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget,
)

from ..theme import Theme
from ..widgets import EmptyState, PageHeader, label


class TaskRow(QFrame):
    def __init__(self, task: dict[str, Any], theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectCard")
        self.setMinimumHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(12)
        body = QVBoxLayout()
        body.setSpacing(3)
        title = QLabel(str(task.get("label") or "下载任务"))
        title.setStyleSheet(
            f"color: {theme.text}; font-size: 12px; font-weight: 600;")
        body.addWidget(title)
        status = QLabel(str(task.get("message") or "准备中…"))
        status.setStyleSheet(f"color: {theme.text_muted}; font-size: 10px;")
        body.addWidget(status)
        layout.addLayout(body, 1)
        percent = int(task.get("percent") or 0)
        value = QLabel(f"{percent}%")
        value.setStyleSheet(
            f"color: {theme.accent}; font-size: 11px; font-weight: 600;")
        value.setFixedWidth(40)
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(value)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(percent)
        bar.setTextVisible(False)
        bar.setFixedWidth(180)
        bar.setFixedHeight(6)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {theme.surface_alt}; border: 0;"
            f" border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {theme.accent}; border-radius: 3px; }}")
        layout.addWidget(bar)


class TasksPage(QWidget):
    def __init__(self, shell: Any, theme: Theme) -> None:
        super().__init__()
        self.shell = shell
        self.theme = theme
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 18)
        outer.setSpacing(13)
        outer.addWidget(PageHeader(
            "", "任务", "这里显示正在下载和安装的项目。"))
        self.empty = EmptyState(
            "没有正在进行的任务", "下载模组或安装游戏版本后，任务会显示在这里。", "list")
        self.empty.set_theme(theme)
        outer.addWidget(self.empty, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.host = QWidget()
        self.rows = QVBoxLayout(self.host)
        self.rows.setContentsMargins(0, 0, 4, 0)
        self.rows.setSpacing(7)
        self.rows.addStretch()
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll, 1)
        self.scroll.hide()

    def set_tasks(self, tasks: dict[str, dict[str, Any]]) -> None:
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for task in tasks.values():
            self.rows.insertWidget(self.rows.count() - 1, TaskRow(task, self.theme))
        self.empty.setVisible(not tasks)
        self.scroll.setVisible(bool(tasks))

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.empty.set_theme(theme)
