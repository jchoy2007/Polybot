"""
PolyBot - Script de Fix para Auto-Seller
==========================================
Desactiva el auto_seller completamente del bot.

QUE HACE:
1. Backup de main.py → main.py.backup_antes_de_fix
2. Edita main.py removiendo 6 referencias al AutoSeller
3. Renombra modules/auto_seller.py → modules/auto_seller.py.disabled
4. Imprime pasos siguientes (git pull de fixes del branch + restart)

USO:
    cd C:\\PolyBot
    python apply_autoseller_fix.py

SEGURIDAD:
- Si algo falla, tu main.py.backup_antes_de_fix tiene la copia original
- El script es idempotente: si ya lo corriste, detecta y avisa
- Solo toca main.py y auto_seller.py. Nada más.
"""

import os
import sys
import shutil
from datetime import datetime

MAIN_PY = "main.py"
AUTO_SELLER = os.path.join("modules", "auto_seller.py")
AUTO_SELLER_DISABLED = os.path.join("modules", "auto_seller.py.disabled")


def log(msg, level="INFO"):
    prefix = {"INFO": "ℹ️ ", "OK": "✅", "WARN": "⚠️ ", "ERROR": "❌"}.get(level, "  ")
    print(f"{prefix} {msg}")


def backup_main_py():
    """Crea backup de main.py antes de modificarlo."""
    backup_path = f"main.py.backup_antes_de_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(MAIN_PY, backup_path)
    log(f"Backup creado: {backup_path}", "OK")
    return backup_path


def read_main_py():
    with open(MAIN_PY, "r", encoding="utf-8") as f:
        return f.read()


def write_main_py(content):
    with open(MAIN_PY, "w", encoding="utf-8") as f:
        f.write(content)


def apply_edits(content):
    """
    Aplica los 6 cambios al contenido de main.py.
    Retorna (nuevo_contenido, lista_de_cambios_aplicados).
    """
    changes = []
    original = content

    # === CAMBIO 1: Remover import de AutoSeller ===
    import_line = "from modules.auto_seller import AutoSeller\n"
    if import_line in content:
        content = content.replace(import_line, "")
        changes.append("1. Removido: from modules.auto_seller import AutoSeller")
    else:
        changes.append("1. SKIP: import ya no existe")

    # === CAMBIO 2: Remover instanciación seller = AutoSeller() ===
    # Buscar la línea exacta con su indentación
    import re
    seller_init_pattern = re.compile(r'^(\s*)seller\s*=\s*AutoSeller\(\)\s*\n', re.MULTILINE)
    match = seller_init_pattern.search(content)
    if match:
        content = seller_init_pattern.sub("", content, count=1)
        changes.append("2. Removido: seller = AutoSeller()")
    else:
        changes.append("2. SKIP: seller = AutoSeller() ya no existe")

    # === CAMBIO 3: Remover seller del signature de run_cycle ===
    # "grinder: CryptoGrinder = None,\n                    seller: AutoSeller = None,"
    seller_param_pattern = re.compile(
        r'(\s*grinder:\s*CryptoGrinder\s*=\s*None,)\s*\n\s*seller:\s*AutoSeller\s*=\s*None,',
        re.MULTILINE
    )
    if seller_param_pattern.search(content):
        content = seller_param_pattern.sub(r'\1', content)
        changes.append("3. Removido: parámetro seller: AutoSeller = None en run_cycle()")
    else:
        changes.append("3. SKIP: parámetro seller ya no está en signature")

    # === CAMBIO 4: Remover el bloque AUTO-SELL en run_cycle ===
    auto_sell_block = '''    # ===== AUTO-SELL: Vender posiciones con take profit/stop loss =====
    if seller and not SAFETY.dry_run:
        try:
            sales = await seller.run_cycle()
            if sales:
                for sale in sales:
                    logger.info(f"   💰 VENTA: {sale['question'][:35]} | {sale['reason']} | +${sale['proceeds']:.2f}")
                    if telegram:
                        await telegram.send(
                            f"💰 VENTA AUTOMATICA\\n"
                            f"{sale['question'][:40]}\\n"
                            f"{sale['reason']}\\n"
                            f"Recibido: ${sale['proceeds']:.2f}"
                        )
        except Exception as e:
            logger.debug(f"   Error auto-sell: {e}")

'''
    if auto_sell_block in content:
        content = content.replace(auto_sell_block, "")
        changes.append("4. Removido: bloque AUTO-SELL completo en run_cycle()")
    else:
        # Intentar versión más flexible (por si los saltos de línea son distintos)
        block_pattern = re.compile(
            r'\s*#\s*=+\s*AUTO-SELL:.*?(?=\n\s*#\s*=+|\n\s*# ===== ACTUALIZAR|\n    # =====)',
            re.DOTALL
        )
        m = block_pattern.search(content)
        if m:
            content = content[:m.start()] + "\n" + content[m.end():]
            changes.append("4. Removido: bloque AUTO-SELL (regex flexible)")
        else:
            changes.append("4. SKIP: bloque AUTO-SELL ya no existe")

    # === CAMBIO 5a: Remover seller de primer run_cycle call ===
    # "grinder, seller, telegram, args.scan_only"
    call1_pattern = re.compile(
        r'(stock_trader,\s*grinder,)\s*seller,(\s*telegram,\s*args\.scan_only)',
    )
    if call1_pattern.search(content):
        content = call1_pattern.sub(r'\1\2', content)
        changes.append("5a. Removido: seller del primer run_cycle() call")
    else:
        changes.append("5a. SKIP: seller ya no está en primer call")

    # === CAMBIO 5b: Remover seller de segundo run_cycle call ===
    # "grinder, seller, telegram)" (sin scan_only)
    call2_pattern = re.compile(
        r'(stock_trader,\s*grinder,)\s*seller,(\s*telegram\))',
    )
    if call2_pattern.search(content):
        content = call2_pattern.sub(r'\1\2', content)
        changes.append("5b. Removido: seller del segundo run_cycle() call")
    else:
        changes.append("5b. SKIP: seller ya no está en segundo call")

    # === CAMBIO 6: Remover await seller.close() ===
    close_pattern = re.compile(r'^\s*await\s+seller\.close\(\)\s*\n', re.MULTILINE)
    if close_pattern.search(content):
        content = close_pattern.sub("", content, count=1)
        changes.append("6. Removido: await seller.close()")
    else:
        changes.append("6. SKIP: await seller.close() ya no existe")

    return content, changes


def rename_auto_seller():
    """Renombra modules/auto_seller.py → modules/auto_seller.py.disabled"""
    if not os.path.exists(AUTO_SELLER):
        if os.path.exists(AUTO_SELLER_DISABLED):
            log(f"{AUTO_SELLER} ya estaba renombrado", "OK")
            return True
        log(f"{AUTO_SELLER} no existe (y tampoco el .disabled)", "WARN")
        return False

    if os.path.exists(AUTO_SELLER_DISABLED):
        log(f"{AUTO_SELLER_DISABLED} ya existe. Borrando el .py normal...", "WARN")
        os.remove(AUTO_SELLER)
        return True

    os.rename(AUTO_SELLER, AUTO_SELLER_DISABLED)
    log(f"Renombrado: {AUTO_SELLER} → {AUTO_SELLER_DISABLED}", "OK")
    return True


def verify_syntax():
    """Verifica que main.py compile (syntax check)."""
    import py_compile
    try:
        py_compile.compile(MAIN_PY, doraise=True)
        log("main.py compila correctamente (syntax OK)", "OK")
        return True
    except py_compile.PyCompileError as e:
        log(f"ERROR de sintaxis en main.py: {e}", "ERROR")
        return False


def main():
    print("=" * 60)
    print("PolyBot - Apply Auto-Seller Fix")
    print("=" * 60)
    print()

    # Verificar que estamos en el directorio correcto
    if not os.path.exists(MAIN_PY):
        log(f"No se encontró {MAIN_PY} en el directorio actual", "ERROR")
        log("Corre este script desde C:\\PolyBot (donde está main.py)", "ERROR")
        sys.exit(1)

    if not os.path.exists("modules"):
        log("No se encontró la carpeta modules/", "ERROR")
        sys.exit(1)

    # Paso 1: Backup
    log("Paso 1: Creando backup de main.py...")
    backup_path = backup_main_py()
    print()

    # Paso 2: Leer y editar main.py
    log("Paso 2: Editando main.py...")
    content = read_main_py()
    new_content, changes = apply_edits(content)

    for change in changes:
        if "SKIP" in change:
            log(f"  {change}", "WARN")
        else:
            log(f"  {change}", "OK")

    if new_content == content:
        log("No hay cambios para aplicar (main.py ya estaba limpio)", "WARN")
    else:
        write_main_py(new_content)
        log("main.py actualizado", "OK")
    print()

    # Paso 3: Verificar sintaxis
    log("Paso 3: Verificando sintaxis de main.py...")
    if not verify_syntax():
        log("Restaurando backup...", "ERROR")
        shutil.copy2(backup_path, MAIN_PY)
        log(f"Restaurado desde {backup_path}", "ERROR")
        sys.exit(1)
    print()

    # Paso 4: Renombrar auto_seller.py
    log("Paso 4: Desactivando auto_seller.py...")
    rename_auto_seller()
    print()

    # Paso 5: Instrucciones finales
    print("=" * 60)
    print("✅ FIX APLICADO EXITOSAMENTE")
    print("=" * 60)
    print()
    print("PROXIMOS PASOS:")
    print()
    print("1. Pullear los fixes del branch (tracker.py + telegram_monitor.py):")
    print()
    print("   git fetch origin claude/polybot-strategy-redeem-fix-MRxIE")
    print("   git checkout origin/claude/polybot-strategy-redeem-fix-MRxIE -- core/tracker.py modules/telegram_monitor.py")
    print()
    print("2. Verificar que main.py arranque sin errores:")
    print()
    print("   python -c \"import main\"")
    print()
    print("3. Reiniciar el bot:")
    print()
    print("   python main.py --live --stop-at 23")
    print()
    print("4. Verificar en el log de arranque:")
    print("   - ✅ 'Recalculados X profits de trades WON'")
    print("   - ✅ Sin ImportError de AutoSeller")
    print("   - ✅ Sin mensajes de 'polybot.seller'")
    print()
    print(f"BACKUP GUARDADO EN: {backup_path}")
    print("Si algo falla, puedes restaurar con:")
    print(f"  copy {backup_path} main.py")
    print()


if __name__ == "__main__":
    main()
