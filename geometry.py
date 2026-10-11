"""Window geometry shared by recognition and publication."""

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
                remaining = []
                for x1,y1,x2,y2 in parts:
                    a,b,c,d = max(x1,front[0]),max(y1,front[1]),min(x2,front[2]),min(y2,front[3])
                    if a >= c or b >= d:
                        remaining.append((x1,y1,x2,y2))
                    else:
                        remaining.extend(box for box in ((x1,y1,x2,b),(x1,d,x2,y2),(x1,b,a,d),(c,b,x2,d))
                                         if box[2] > box[0] and box[3] > box[1])
                parts = remaining
            result.extend({"x":x,"y":y,"w":r-x,"h":b-y} for x,y,r,b in parts)
        foreground.append((w["x"],w["y"],w["x"]+w["w"],w["y"]+w["h"]))
    return result

