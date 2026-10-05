"""CATIA V5 에서 열려 있는 CATPart(디자인 1차 스킨)를 STL 로 내보내는 보조 스크립트 (Windows + pywin32 필요).

사용:  python scripts/catia_export_skin.py "D:\\작업\\GL3_PE2_skin.stl" [--per-set]

* 기본: 현재 '보이는' 지오메트리 전체를 하나의 STL 로 저장한다. (CATIA STL 출력은 숨긴(Hide) 요소를 제외하므로,
  내보내기 전에 작업용 서피스는 숨기고 최종 외관면만 보이게 해두면 결과가 깨끗하다.)
* --per-set: Geometrical Set(HybridBody) 하나씩 따로 보이게 한 뒤 각각 STL 로 저장한다.
  → 결과 폴더를 `segment --skin 폴더` 로 넘기면 조각(파일) 단위 다수결이 적용돼 경계가 더 깨끗해진다.
  (CATIA 자동화가 cat-mcp 로 이미 되어 있다면, 같은 로직을 cat-mcp 쪽에 옮겨 써도 된다.)

주의: STL 해상도는 CATIA 옵션(Tools > Options > General > Display > Performance > 3D Accuracy)에 따른다.
      0.2mm(fixed) 정도면 충분하다. 너무 거칠면 작은 부품 경계가 흐려진다.
"""
import os
import sys
import time


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    out = sys.argv[1]
    per_set = "--per-set" in sys.argv
    try:
        import win32com.client  # type: ignore
    except ImportError:
        print("pywin32 가 필요합니다: pip install pywin32 (오프라인이면 wheels 폴더에서)")
        return
    catia = win32com.client.Dispatch("CATIA.Application")
    doc = catia.ActiveDocument
    part = doc.Part
    sel = doc.Selection
    if not per_set:
        doc.ExportData(out, "stl")
        print("저장:", out)
        return
    os.makedirs(out, exist_ok=True)
    hbs = part.HybridBodies
    names = [hbs.Item(i + 1).Name for i in range(hbs.Count)]
    print(f"Geometrical Set {len(names)}개:", names)
    for i, name in enumerate(names):
        # 모두 숨기고 하나만 보이게
        sel.Clear()
        for j in range(hbs.Count):
            sel.Add(hbs.Item(j + 1))
        sel.VisProperties.SetShow(1)  # 1 = Hide
        sel.Clear()
        sel.Add(hbs.Item(i + 1))
        sel.VisProperties.SetShow(0)  # 0 = Show
        sel.Clear()
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        path = os.path.join(out, f"{i:02d}_{safe}.stl")
        doc.ExportData(path, "stl")
        time.sleep(0.3)
        print("저장:", path)
    # 원상복구(전부 보이기)
    sel.Clear()
    for j in range(hbs.Count):
        sel.Add(hbs.Item(j + 1))
    sel.VisProperties.SetShow(0)
    sel.Clear()


if __name__ == "__main__":
    main()
