"""한국어가 길어져 넘친 만큼 아래 내용(문단·그림·표)을 밀어 내리는 재배치 계획.

페이지를 '전체 폭 구역'(제목, 초록, 두 단에 걸친 그림 등)과 '단 구역'으로 나눈다.
단 구역에서는 왼쪽·오른쪽 단을 따로 밀고, 다음 구역은 두 단 중 더 많이 밀린 만큼 내려간다.
원본 페이지는 가로 띠(strip)로 잘라 각자의 이동량만큼 내려 그린다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass
class Item:
    rect: pymupdf.Rect
    growth: float = 0.0  # 이 요소 아래로 더 필요한 높이


@dataclass
class Strip:
    clip: pymupdf.Rect
    dy: float


@dataclass
class PagePlan:
    strips: list[Strip]
    extension: float  # 페이지가 세로로 늘어나는 양

    @property
    def moved(self) -> bool:
        return self.extension > 0.01 or any(abs(s.dy) > 0.01 for s in self.strips)

    def shift_at(self, x: float, y: float) -> float:
        for s in self.strips:
            if s.clip.x0 <= x < s.clip.x1 and s.clip.y0 <= y < s.clip.y1:
                return s.dy
        return 0.0


def _column(items: list[Item], y0: float, y1: float, x0: float, x1: float):
    """한 단 안의 띠 목록, 총 늘어남, 원래 내용의 끝 y."""
    cuts: list[tuple[float, float]] = []
    for g in sorted((i for i in items if i.growth > 0), key=lambda i: i.rect.y1):
        cut = g.rect.y1
        changed = True
        while changed:  # 걸쳐 있는 요소를 자르지 않도록 자르는 위치를 내린다
            changed = False
            for it in items:
                # 몇 pt 겹치는 정도(인접한 줄 상자)는 걸친 것으로 보지 않는다
                if it is not g and it.growth == 0 and it.rect.y0 < cut - 5 and it.rect.y1 > cut + 1:
                    cut = it.rect.y1
                    changed = True
        # 바로 아래 요소가 살짝 겹쳐 시작하면 그 위에서 자른다
        near = [it.rect.y0 for it in items if it is not g and cut - 5 < it.rect.y0 <= cut]
        if near:
            cut = min(near)
        cuts.append((cut, g.growth))
    cuts.sort()
    bounds = [y0] + [c for c, _ in cuts if y0 < c < y1] + [y1]
    strips = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < 0.01:
            continue
        dy = sum(g for c, g in cuts if c <= a + 0.01)
        strips.append((pymupdf.Rect(x0, a, x1, b), dy))
    total = sum(g for _, g in cuts)
    end = max((i.rect.y1 for i in items), default=y0)
    return strips, total, end


def plan_page(page_rect: pymupdf.Rect, items: list[Item]) -> PagePlan:
    W, H = page_rect.width, page_rect.height
    mid = W / 2

    def is_full(i: Item) -> bool:
        return i.rect.x0 < mid - 10 and i.rect.x1 > mid + 10

    # 전체 폭 요소들의 세로 구간을 합친다
    blocks: list[list[float]] = []
    for i in sorted((i for i in items if is_full(i)), key=lambda i: i.rect.y0):
        if blocks and i.rect.y0 <= blocks[-1][1] + 1:
            blocks[-1][1] = max(blocks[-1][1], i.rect.y1)
        else:
            blocks.append([i.rect.y0, i.rect.y1])

    zones: list[tuple[str, float, float]] = []
    prev = 0.0
    for a, b in blocks:
        if a > prev:
            zones.append(("col", prev, a))
        zones.append(("full", max(a, prev), b))
        prev = b
    if prev < H:
        zones.append(("col", prev, H))
    # 페이지 밖으로 나간 요소가 있어도 마지막 구역이 끝까지 덮도록
    zones[-1] = (zones[-1][0], zones[-1][1], max(zones[-1][2], H))

    def zone_of(i: Item) -> int:
        cy = (i.rect.y0 + i.rect.y1) / 2
        if is_full(i):
            for k, (kind, a, b) in enumerate(zones):
                if kind == "full" and a - 0.5 <= cy <= b + 0.5:
                    return k
        for k, (kind, a, b) in enumerate(zones):
            if a <= cy < b:
                return k
        return len(zones) - 1

    by_zone: dict[int, list[Item]] = {}
    for i in items:
        by_zone.setdefault(zone_of(i), []).append(i)

    strips: list[Strip] = []
    base = 0.0
    for k, (kind, a, b) in enumerate(zones):
        zitems = by_zone.get(k, [])
        if kind == "full":
            col_strips, total, _ = _column(zitems, a, b, 0, W)
            strips += [Strip(r, base + dy) for r, dy in col_strips]
            base += total
            continue
        left = [i for i in zitems if (i.rect.x0 + i.rect.x1) / 2 < mid]
        right = [i for i in zitems if (i.rect.x0 + i.rect.x1) / 2 >= mid]
        gutter = mid
        if left and right:
            lx = max(i.rect.x1 for i in left)
            rx = min(i.rect.x0 for i in right)
            if lx < rx:
                gutter = (lx + rx) / 2
        ends = []
        for col, x0, x1 in ((left, 0, gutter), (right, gutter, W)):
            col_strips, total, end = _column(col, a, b, x0, x1)
            strips += [Strip(r, base + dy) for r, dy in col_strips]
            ends.append((end, total))
        # 짧은 단의 아래 여백은 흡수하고, 가장 긴 단이 늘어난 만큼만 다음 구역을 민다
        orig = max(e for e, _ in ends)
        grown = max(e + t for e, t in ends)
        base += max(0.0, grown - orig)
    return PagePlan(strips, base)
