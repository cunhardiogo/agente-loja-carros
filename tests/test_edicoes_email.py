from app import edicoes



def _len(n: int, dado: bytes) -> bytes:
    tam, out = len(dado), b""
    while True:
        b = tam & 0x7F
        tam >>= 7
        out += bytes([b | (0x80 if tam else 0)])
        if not tam:
            break
    return bytes([(n << 3) | 2]) + out + dado


def test_edicao_com_email_no_texto():
    texto = "Resumo de Venda\n\n👨‍💻 Vendedor: Edson\nEMAIL: cliente@hotmail.com\n" + "x" * 150
    plano = _len(1, _len(1, b"ABC123") + _len(2, _len(1, texto.encode())))
    assert edicoes._texto_editado(plano + _len(3, b"123@lid"), "ABC123") == texto.strip()
