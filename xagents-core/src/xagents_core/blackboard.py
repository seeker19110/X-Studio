"""Blackboard (`shared-context`): tri thức chung theo namespace, mỗi namespace một chủ, phân vùng theo dự án
(K3.6b của ADR gốc 0001).

**Hình dạng lệch: một bên thiếu, giống K3.5b.** `difflib` cho **0.092** trên 114 dòng company vs 30 dòng
studio, và chín hàm chỉ company có. Nhưng con số thấp ấy KHÔNG nói hai blackboard khác bản chất: cả hai làm
đúng một việc — nghe `shared-context`, giữ bản có `version` lớn nhất cho mỗi namespace, và `write` là
đọc-version-rồi-publish. Studio chỉ chưa làm phần còn lại: không mirror ra file, không phân vùng theo dự án,
không khoá, không `content`. Nên core giữ **toàn bộ cơ chế** và studio được nâng theo, đúng như K3.5b.

Hai tính chất core phải giữ cùng lúc:

- **Toàn văn, không phải con trỏ** (ADR-0012): mỗi bản ghi mang `content` đi qua bus (nguồn sự thật, replay
  dựng lại được) và được mirror ra file trong artifact store để người đọc và diff.
- **Phân vùng theo dự án** (ADR-0018): một artifact thuộc về một `project_id`; chỉ namespace trong
  `cfg.global_namespaces` là phạm vi toàn công ty. Không phân vùng thì hai khách chạy trên cùng bus sẽ ghi đè
  `prd`/`architecture` của nhau.

Ba thứ mỗi công ty đưa vào, tất cả là **dữ liệu hoặc lớp**, không phải nhánh `if`:

- `cfg.global_namespaces` — namespace không thuộc dự án nào (company: `knowledge`).
- `envelope_cls` / `context_cls` — lớp `Envelope` và `SharedContext` của công ty. Cùng lý do `envelope_cls` ở
  bus và `spec_cls` ở registry: dựng bằng lớp core là làm rơi trường của lớp con im lặng (`rulings` của company).
- `EXT` — phần mở rộng file mirror theo namespace. Chỉ có nghĩa khi công ty dùng `store`; mặc định markdown.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Generic, TypeVar

from .bus import InMemoryBus
from .config import CoreConfig
from .events import Envelope, SharedContext

__all__ = ["Blackboard", "Scope"]

E = TypeVar("E", bound=Envelope)
C = TypeVar("C", bound=SharedContext)

Scope = tuple[str | None, str]  # (project_id hoặc None nếu toàn công ty, namespace)


class Blackboard(Generic[E, C]):
    """Đọc/ghi shared-context. Ghi đi qua bus nên được kiểm quyền owner. Trạng thái giữ theo (project_id, namespace)."""

    #: Lớp con đặt lại — xem docstring module.
    envelope_cls: type[E]
    context_cls: type[C]
    #: Phần mở rộng file mirror theo namespace; namespace không có tên ở đây thì là markdown.
    EXT: ClassVar[Mapping[str, str]] = {}

    def __init__(self, cfg: CoreConfig, bus: InMemoryBus[E], store: Path | None = None):
        self.cfg = cfg
        self.bus = bus
        self.store = Path(store) if store else None
        self._latest: dict[Scope, C] = {}
        # Đánh số version là đọc-sửa-ghi: hai agent cùng sở hữu một namespace (vd. `api-contract` của product và
        # builder) chạy song song (--workers > 1) mà không khoá thì cả hai cùng ra v1, bản sau bị `_on` bỏ vì không
        # lớn hơn — mất hẳn một artifact. Khoá giữ suốt đọc version → publish; `_on` chạy trong publish, không lấy khoá.
        self._wlock = threading.RLock()
        bus.subscribe("shared-context", self._on)

    def scope_of(self, namespace: str, project_id: str | None) -> Scope:
        return (None if namespace in self.cfg.global_namespaces else project_id, namespace)

    def context_key(self, namespace: str, project_id: str | None) -> str:
        """Key của event shared-context: namespace toàn công ty giữ nguyên tên, còn lại có tiền tố dự án."""
        pid, ns = self.scope_of(namespace, project_id)
        return ns if pid is None else f"{pid}/{ns}"

    def _scope(self, sc: C) -> Scope:
        return self.scope_of(sc.namespace, sc.project_id)

    def _on(self, env: E) -> None:
        sc = self.context_cls.model_validate(env.payload)
        cur = self._latest.get(self._scope(sc))
        if cur is None or sc.version > cur.version:
            self._latest[self._scope(sc)] = sc
            if self.store is not None and sc.content is not None:
                self._mirror(sc)

    def path(self, namespace: str, version: int | None = None, project_id: str | None = None) -> Path | None:
        """Đường dẫn file mirror của một bản (mặc định bản mới nhất); None nếu không có store."""
        if self.store is None: return None
        pid, ns = self.scope_of(namespace, project_id)
        ext = self.EXT.get(ns, "md")
        base = self.store if pid is None else self.store / pid
        return base / ns / (f"v{version}.{ext}" if version else f"latest.{ext}")

    def _mirror(self, sc: C) -> None:
        for p in (self.path(sc.namespace, sc.version, sc.project_id), self.path(sc.namespace, None, sc.project_id)):
            assert p is not None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(sc.content or "", encoding="utf-8", newline="\n")

    def write(self, actor: str, namespace: str, content_ref: str, summary: str = "", content: str | None = None,
              project_id: str | None = None) -> C:
        scope = self.scope_of(namespace, project_id)
        with self._wlock:
            v = (self._latest[scope].version + 1) if scope in self._latest else 1
            sc = self.context_cls(namespace=namespace, version=v, content_ref=content_ref,
                                  summary=summary, content=content, project_id=scope[0])
            self.bus.publish(self.envelope_cls(topic="shared-context", key=self.context_key(namespace, project_id),
                                               actor=actor, payload=sc.model_dump()))
        return sc

    def read(self, namespace: str, project_id: str | None = None) -> C | None:
        return self._latest.get(self.scope_of(namespace, project_id))

    def content(self, namespace: str, project_id: str | None = None) -> str | None:
        """Toàn văn bản mới nhất (None nếu chưa có hoặc chỉ có con trỏ)."""
        sc = self.read(namespace, project_id)
        return sc.content if sc else None

    def all(self) -> dict[str, C]:
        """Mọi bản ghi mới nhất, khoá `<project>/<namespace>` (hoặc namespace trần nếu toàn công ty) — cho `status`."""
        return {f"{pid}/{ns}" if pid else ns: sc
                for (pid, ns), sc in sorted(self._latest.items(), key=lambda kv: (kv[0][0] or "", kv[0][1]))}

    def overview(self) -> dict[str, str]:
        """Toàn bộ blackboard cho lệnh `status`: khoá là `<project>/<namespace>` (hoặc namespace trần nếu toàn công ty)."""
        return {k: sc.content_ref for k, sc in self.all().items()}

    def snapshot(self, project_id: str | None = None) -> dict[str, C]:
        """Bản mới nhất mỗi namespace TRONG phạm vi một dự án, cộng các namespace toàn công ty.
        `project_id=None` trả về mọi thứ không thuộc dự án nào (dùng cho demo/eval)."""
        return {ns: sc for (pid, ns), sc in self._latest.items() if pid is None or pid == project_id}

    def rehydrate(self) -> None:
        """Dựng lại từ bus (mở lại SQLite): áp mọi bản ghi theo thứ tự, mirror bản mới nhất."""
        for env in self.bus.replay(topic="shared-context"):
            self._on(env)
