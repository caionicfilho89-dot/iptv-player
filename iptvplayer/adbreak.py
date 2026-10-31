"""Pular intervalo: percebe o comercial pela marca do canal no canto da tela.

Quase todo canal mostra o logotipo num canto durante o programa e tira no intervalo. No primeiro minuto o app
aprende onde ele fica (pixels de um canto que quase não mudam enquanto o resto do vídeo muda); depois, quando
a marca some por alguns segundos, é intervalo — tira o som — e quando volta, devolve o som.
Os quadros vêm de fotos pequenas do VLC (video_take_snapshot) a cada 2 s."""
import numpy as np

LEARN_FRAMES = 30          # ~1 min de quadros para achar a marca
STABLE_STD = 10.0          # pixel da marca: varia menos que isso ao longo do tempo
MIN_LOGO_PIXELS = 60       # menos que isso num canto não é marca
GONE_DIFF = 38.0           # diferença média (0-255) na área da marca: acima disso ela sumiu
BREAK_AFTER = 4            # quadros seguidos sem a marca (≈8 s) = intervalo
BACK_AFTER = 2             # quadros seguidos com a marca (≈4 s) = voltou o programa


def corners(img):
    """Os 4 cantos (22% da largura x 20% da altura) de um quadro em tons de cinza [h, w]."""
    h, w = img.shape
    ch, cw = max(8, h // 5), max(8, int(w * 0.22))
    return {"tl": img[:ch, :cw], "tr": img[:ch, w - cw:], "bl": img[h - ch:, :cw], "br": img[h - ch:, w - cw:]}


class LogoDetector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.frames = []
        self.corner = None         # canto onde está a marca
        self.mask = None
        self.ref = None
        self.gone = self.back = 0
        self.in_break = False
        self.failed = False

    @property
    def learning(self):
        return self.mask is None and not self.failed

    def feed(self, gray):
        """Um quadro (numpy uint8 [h, w]). Devolve "start" quando começa o intervalo, "end" quando acaba, ou None."""
        if self.failed:
            return None
        if self.mask is None:
            self.frames.append(gray.astype(np.float32))
            if len(self.frames) >= LEARN_FRAMES:
                self._learn()
            return None
        part = corners(gray.astype(np.float32))[self.corner]
        if part.shape != self.ref.shape:
            return None   # resolução mudou
        diff = float(np.abs(part[self.mask] - self.ref[self.mask]).mean())
        if diff > GONE_DIFF:
            self.gone, self.back = self.gone + 1, 0
            if not self.in_break and self.gone >= BREAK_AFTER:
                self.in_break = True
                return "start"
        else:
            self.back, self.gone = self.back + 1, 0
            if self.in_break and self.back >= BACK_AFTER:
                self.in_break = False
                return "end"
        return None

    def _learn(self):
        shapes = {f.shape for f in self.frames}
        if len(shapes) != 1:
            self.frames = self.frames[-5:]
            return
        stack = np.stack(self.frames)
        best = None
        for name in ("tl", "tr", "bl", "br"):
            region = np.stack([corners(f)[name] for f in stack])
            std, mean = region.std(0), region.mean(0)
            moving = float(std.mean())   # o resto do canto precisa mexer, senão é tarja preta/tela parada
            # marca: estável, nem preta nem num fundo liso
            mask = (std < STABLE_STD) & (mean > 40)
            n = int(mask.sum())
            if n >= MIN_LOGO_PIXELS and moving > STABLE_STD * 1.5 and n < mask.size * 0.6:
                score = n * moving
                if best is None or score > best[0]:
                    best = (score, name, mask, mean)
        self.frames = []
        if best is None:
            self.failed = True
            return
        _s, self.corner, self.mask, self.ref = best
