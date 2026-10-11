"""Window geometry shared by recognition and publication."""

def subtract_box(box, front):
    x1,y1,x2,y2 = box
    a,b,c,d = max(x1,front[0]),max(y1,front[1]),min(x2,front[2]),min(y2,front[3])
    if a >= c or b >= d:
        return [box]
    return [part for part in ((x1,y1,x2,b),(x1,d,x2,y2),(x1,b,a,d),(c,b,x2,d))
            if part[2] > part[0] and part[3] > part[1]]


def owning_windows(windows):
    """Retain every possible first-containing OCR owner, in original order.

    This assigns text context, never pixel opacity. A fully covered rectangle
    cannot own a row midpoint under the redactor's existing ownership rule.
    All original windows still participate in the independent privacy masks.
    """
    result, foreground = [], []
    for w in windows:
        box = (w["x"],w["y"],w["x"]+w["w"],w["y"]+w["h"])
        parts = [box]
        for front in foreground:
            parts = [part for existing in parts for part in subtract_box(existing, front)]
            if not parts:
                break
        if parts:
            result.append(w)
        foreground.append(box)
    return result


def visible_private_regions(windows, private_ids, excluded_ids=()):
    """Clip only windows already excluded from the captured compositor pixels.

    A foreground box cannot establish per-pixel opacity. Without an exclusion
    receipt, even a transparent foreground must leave the full private mask.
    """
    result, foreground = [], []
    for w in windows:
        parts = [(w["x"], w["y"], w["x"]+w["w"], w["y"]+w["h"])]
        if w.get("id") in private_ids:
            for front in foreground if w.get("id") in excluded_ids else ():
                parts = [part for existing in parts for part in subtract_box(existing, front)]
            result.extend({"x":x,"y":y,"w":r-x,"h":b-y} for x,y,r,b in parts)
        foreground.append((w["x"],w["y"],w["x"]+w["w"],w["y"]+w["h"]))
    return result

