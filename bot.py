import io
import time
import hmac
import hashlib
import sqlite3
import requests
from urllib.parse import urlencode
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat

# =====================================================================
# 🔴 ZONA DE REEMPLAZO: CAMBIA ESTOS 7 DATOS POR LOS TUYOS 🔴
# =====================================================================

# 1. El Token que te dio @BotFather (entre comillas)
TELEGRAM_TOKEN = "9085229716:AAHYsLL2wmOwYJGweQACxLv5jp5ltiQLnXI"

# 2. Tu ID numérico de Telegram (SIN comillas, solo números)
ADMIN_ID = 8499104949

# 3. Tu usuario de Telegram SIN el @ (para el botón de Soporte)
ADMIN_USERNAME = "uservipxd"

# 4. Tu Binance Pay ID (entre comillas)
BINANCE_PAY_ID = "884195829"

# 5. Tu API Key de Binance (entre comillas)
BINANCE_API_KEY = "NCztoP5SVCX71cehqZeKTdEOntTGpYDRw3M0NNpSEgxFifgtiMErhbsSIcnVn4TW"

# 6. Tu Secret Key de Binance (entre comillas)
BINANCE_SECRET_KEY = "31skkrCvejqUclA2tn5A5gprMZ3t8HvfVn1FTKYZvpn5eqUbEyG2de5ftR5oW4tL"

# 7. Tu catálogo y precios en USDT
CATALOGO = {
    "Gemini Pro": 3.00,
    "ChatGPT Plus": 4.50,
    "Netflix Completa": 6.50,
    "Disney+ Completa": 4.00,
    "Max Completa": 3.50,
    "Prime Video Completa": 3.00,
    "Spotify Individual": 2.00,
    "Crunchyroll Mega Fan": 2.50,
    "Canva Pro": 1.50
}

# Productos que piden el correo al cliente para enviarle invitación
PRODUCTOS_A_DOMINIO = ["Canva Pro"]

# =====================================================================
# ⚙️ MOTOR DEL BOT (NO NECESITAS TOCAR NADA DE AQUÍ PARA ABAJO) ⚙️
# =====================================================================

bot = telebot.TeleBot(TELEGRAM_TOKEN)

esperando_pago = set()             # Usuarios enviando Order ID de Binance
estado_carga_admin = {}            # Admin usando /cargar
esperando_correo_dominio = {}      # {user_id: producto} cuando compran Canva Pro
esperando_ticket_soporte = set()   # Usuarios escribiendo a soporte
estado_notificacion_admin = {}     # {admin_id: target_user_id} para /enviar individual
esperando_noti_global = set()      # Admin usando /noti para enviar anuncio a todos

def init_db():
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    nombre TEXT,
                    saldo REAL DEFAULT 0.0,
                    compras INTEGER DEFAULT 0,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
    c.execute("""CREATE TABLE IF NOT EXISTS stock (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    producto TEXT,
                    credenciales TEXT,
                    vendido INTEGER DEFAULT 0
                )""")
    c.execute("""CREATE TABLE IF NOT EXISTS pagos_usados (
                    order_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    monto REAL,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
    c.execute("""CREATE TABLE IF NOT EXISTS historial_compras (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    producto TEXT,
                    credenciales TEXT,
                    precio REAL,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
    for col_sql in [
        "ALTER TABLE usuarios ADD COLUMN username TEXT",
        "ALTER TABLE usuarios ADD COLUMN nombre TEXT",
        "ALTER TABLE usuarios ADD COLUMN compras INTEGER DEFAULT 0",
        "ALTER TABLE usuarios ADD COLUMN fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    ]:
        try:
            c.execute(col_sql)
        except Exception:
            pass
    conn.commit()
    conn.close()

init_db()

def configurar_menu_comandos():
    """Configura el botón de menú de Telegram para clientes y para el Admin."""
    try:
        comandos_publicos = [
            BotCommand("menu", "🏠 Abrir el Menú Principal"),
            BotCommand("buy", "🛒 Ver Catálogo y Comprar"),
            BotCommand("stock", "📊 Ver disponibilidad del catálogo")
        ]
        bot.set_my_commands(comandos_publicos)

        comandos_admin = comandos_publicos + [
            BotCommand("cmds", "👑 Ver todos los comandos de Admin"),
            BotCommand("cargar", "📥 Subir cuentas o links al stock"),
            BotCommand("noti", "📢 Enviar anuncio global a todos"),
            BotCommand("historial", "📜 Ver compras de un usuario"),
            BotCommand("stats", "📈 Estadísticas y ventas de la tienda")
        ]
        bot.set_my_commands(comandos_admin, scope=BotCommandScopeChat(ADMIN_ID))
    except Exception:
        pass

configurar_menu_comandos()

def registrar_o_actualizar_usuario(user):
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    uname = user.username.lower() if user.username else ""
    nombre = user.first_name or "Usuario"
    saldo_inicial = 999999.0 if user.id == ADMIN_ID else 0.0
    c.execute("INSERT OR IGNORE INTO usuarios (user_id, username, nombre, saldo, compras) VALUES (?, ?, ?, ?, 0)",
              (user.id, uname, nombre, saldo_inicial))
    if user.id == ADMIN_ID:
        c.execute("UPDATE usuarios SET username = ?, nombre = ?, saldo = 999999.0 WHERE user_id = ?", (uname, nombre, user.id))
    else:
        c.execute("UPDATE usuarios SET username = ?, nombre = ? WHERE user_id = ?", (uname, nombre, user.id))
    conn.commit()
    conn.close()

def obtener_datos_usuario(user_id):
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    c.execute("SELECT user_id, username, nombre, saldo, compras FROM usuarios WHERE user_id = ?", (user_id,))
    fila = c.fetchone()
    conn.close()
    if not fila:
        sal_def = 999999.0 if user_id == ADMIN_ID else 0.0
        return user_id, "Sin @username", "Cliente", sal_def, 0
    uid, uname, nom, sal, comp = fila
    if uid == ADMIN_ID:
        sal = 999999.0
    return uid, (f"@{uname}" if uname else "Sin @username"), (nom or "Cliente"), round(sal or 0.0, 2), (comp or 0)

def buscar_id_por_id_o_username(identificador):
    limpio = identificador.strip().lstrip("@").lower()
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    if limpio.isdigit():
        c.execute("SELECT user_id FROM usuarios WHERE user_id = ?", (int(limpio),))
    else:
        c.execute("SELECT user_id FROM usuarios WHERE LOWER(username) = ?", (limpio,))
    fila = c.fetchone()
    conn.close()
    if fila:
        return fila[0]
    return int(limpio) if limpio.isdigit() else None

def contar_stock(producto):
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM stock WHERE producto = ? AND vendido = 0", (producto,))
    cantidad = c.fetchone()[0]
    conn.close()
    return cantidad

def buscar_producto_nombre(texto_busqueda):
    texto_limpio = texto_busqueda.strip().lower()
    for prod in CATALOGO.keys():
        if prod.lower() == texto_limpio or texto_limpio in prod.lower():
            return prod
    return None

def limpiar_estados_usuario(user_id):
    esperando_pago.discard(user_id)
    estado_carga_admin.pop(user_id, None)
    esperando_correo_dominio.pop(user_id, None)
    esperando_ticket_soporte.discard(user_id)
    estado_notificacion_admin.pop(user_id, None)
    esperando_noti_global.discard(user_id)

def boton_volver_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🏠 Ir al Menú Principal", callback_data="inicio"))
    return markup

def enviar_archivo_txt(chat_id, contenido_texto, nombre_archivo, caption=""):
    archivo_memoria = io.BytesIO(contenido_texto.encode("utf-8"))
    archivo_memoria.name = nombre_archivo
    bot.send_document(chat_id, archivo_memoria, caption=caption, parse_mode="Markdown", reply_markup=boton_volver_menu())

def verificar_pago_binance(order_id_cliente):
    url = "https://api.binance.com/sapi/v1/pay/transactions"
    timestamp = int(time.time() * 1000)
    query_string = urlencode({"timestamp": timestamp, "limit": 50})
    
    signature = hmac.new(
        BINANCE_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    full_url = f"{url}?{query_string}&signature={signature}"
    
    try:
        resp = requests.get(full_url, headers=headers, timeout=10).json()
        if not resp.get("success"):
            return False, "Error de conexión con la API de Binance. Revisa tus API Keys."
        
        for tx in resp.get("data", []):
            tx_order_id = str(tx.get("orderId", ""))
            tx_prepay_id = str(tx.get("prepayId", ""))
            monto = float(tx.get("amount", 0))
            moneda = tx.get("currency", "")
            
            id_limpio = str(order_id_cliente).strip()
            if (id_limpio == tx_order_id or id_limpio == tx_prepay_id) and monto > 0:
                if moneda != "USDT":
                    return False, f"Enviaste {moneda}. El bot solo procesa pagos en USDT."
                return True, monto
                
        return False, "No se encontró ese ID de orden en nuestros pagos recientes."
    except Exception as e:
        return False, f"Error de red: {e}"

# ================= VISTAS DE MENÚ Y CATÁLOGO =================
def vista_menu_principal(user):
    registrar_o_actualizar_usuario(user)
    uid, uname, nombre, saldo, _ = obtener_datos_usuario(user.id)
    rango = "👑 ADMINISTRADOR" if uid == ADMIN_ID else "👤 Cliente"
    texto = (
        f"⚡ *SA SHOP — TIENDA AUTOMÁTICA 24/7* ⚡\n\n"
        f"👋 Hola, *{nombre}* ({uname})\n"
        f"🎖️ *Rol:* {rango}\n"
        f"🆔 *Tu ID:* `{uid}`\n"
        f"💰 *Tu Saldo:* `{saldo:.2f} USDT`\n\n"
        f"Selecciona una opción en los botones de abajo:"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🛒 Ver Catálogo y Comprar", callback_data="catalogo"),
        InlineKeyboardButton("💎 Recargar Saldo (Binance Pay)", callback_data="recargar"),
        InlineKeyboardButton("👤 Mi Cuenta / Mi Historial", callback_data="perfil"),
        InlineKeyboardButton("🛠️ Soporte y Ayuda", callback_data="soporte")
    )
    if uid == ADMIN_ID:
        markup.add(InlineKeyboardButton("👑 Panel de Comandos Admin (/cmds)", callback_data="panel_admin"))
    return texto, markup

def vista_catalogo(user_id):
    _, _, _, saldo, _ = obtener_datos_usuario(user_id)
    markup = InlineKeyboardMarkup(row_width=1)
    for prod, precio in CATALOGO.items():
        if prod in PRODUCTOS_A_DOMINIO:
            texto_boton = f"🟢 {prod} — {precio:.2f} USDT (A tu correo)"
        else:
            disp = contar_stock(prod)
            if disp > 0:
                texto_boton = f"🟢 {prod} — {precio:.2f} USDT ({disp} disp.)"
            else:
                texto_boton = f"🔴 {prod} — {precio:.2f} USDT (Agotado)"
        markup.add(InlineKeyboardButton(texto_boton, callback_data=f"buy_{prod}"))
    markup.add(InlineKeyboardButton("🏠 Volver al Menú Principal", callback_data="inicio"))
    
    texto = (
        f"📺 *CATÁLOGO DE PLATAFORMAS*\n"
        f"💰 *Tu saldo disponible:* `{saldo:.2f} USDT`\n\n"
        f"🟢 = Disponible | 🔴 = Agotado\n"
        f"Toca la plataforma que deseas adquirir:"
    )
    return texto, markup

# Ahora /start, /menu y /menú abren siempre el menú principal
@bot.message_handler(commands=["start", "menu", "menú", "inicio"])
def cmd_start(message):
    limpiar_estados_usuario(message.from_user.id)
    texto, markup = vista_menu_principal(message.from_user)
    bot.send_message(message.chat.id, texto, reply_markup=markup, parse_mode="Markdown")

# Y /buy o /catalogo abren directo el catálogo de compras
@bot.message_handler(commands=["buy", "catalogo", "catálogo"])
def cmd_buy_directo(message):
    registrar_o_actualizar_usuario(message.from_user)
    limpiar_estados_usuario(message.from_user.id)
    texto, markup = vista_catalogo(message.from_user.id)
    bot.send_message(message.chat.id, texto, reply_markup=markup, parse_mode="Markdown")

# ================= COMANDOS EXCLUSIVOS DEL ADMINISTRADOR =================
TEXTO_CMDS_ADMIN = (
    "👑 *PANEL DE COMANDOS DEL ADMINISTRADOR* 👑\n\n"
    "📦 *GESTIÓN DE STOCK:*\n"
    "• `/cargar` — Abre botones para subir cuentas (`correo:clave` o links).\n"
    "• `/stock` — Muestra resumen en texto y te envía un `.txt` con todas las cuentas.\n"
    "• `/stock nombre` — Ejemplo: `/stock gemini` (envía `.txt` de esa plataforma).\n"
    "• `/borrar ID` — Borra una cuenta específica del stock (el ID sale en el `.txt`).\n"
    "• `/vaciar nombre` — Vacía todo el stock no vendido de una plataforma.\n\n"
    "📢 *NOTIFICACIONES Y ANUNCIOS:*\n"
    "• `/noti` — Envía una notificación GLOBAL (foto o texto) a TODOS los usuarios del bot.\n"
    "• `/enviar ID_o_@user` — Envía una notificación individual (foto o texto) a un cliente.\n\n"
    "👥 *USUARIOS, SALDOS Y VENTAS:*\n"
    "• `/historial ID_o_@user` — Muestra compras de un cliente y genera `.txt` con sus cuentas.\n"
    "• `/dar_saldo ID_o_@user MONTO` — Suma saldo USDT a un usuario.\n"
    "• `/quitar_saldo ID_o_@user MONTO` — Resta saldo USDT a un usuario.\n"
    "• `/stats` — Muestra ganancias, total de clientes y ventas.\n"
    "• `/usuarios` — Descarga un archivo `.txt` con todos los clientes registrados."
)

@bot.message_handler(commands=["cmds", "admin", "ayuda"])
def cmd_cmds_admin(message):
    if message.from_user.id != ADMIN_ID:
        return
    bot.reply_to(message, TEXTO_CMDS_ADMIN, reply_markup=boton_volver_menu(), parse_mode="Markdown")

@bot.message_handler(commands=["stock"])
def cmd_consultar_stock(message):
    registrar_o_actualizar_usuario(message.from_user)
    partes = message.text.split(maxsplit=1)
    es_admin = (message.from_user.id == ADMIN_ID)
    
    # 1. Si escribe solo "/stock"
    if len(partes) == 1:
        lineas = ["📊 *ESTADO ACTUAL DEL CATÁLOGO:*\n"]
        for prod, precio in CATALOGO.items():
            if prod in PRODUCTOS_A_DOMINIO:
                lineas.append(f"🟢 *{prod}:* `Activación a correo` ({precio:.2f} USDT)")
            else:
                cant = contar_stock(prod)
                color = "🟢" if cant > 0 else "🔴"
                lineas.append(f"{color} *{prod}:* `{cant}` disponibles ({precio:.2f} USDT)")
        
        bot.reply_to(message, "\n".join(lineas), reply_markup=boton_volver_menu(), parse_mode="Markdown")

        # Si es el Admin, además le genera el archivo .txt con todas las credenciales en stock
        if es_admin:
            conn = sqlite3.connect("tienda.db")
            c = conn.cursor()
            c.execute("SELECT id, producto, credenciales FROM stock WHERE vendido = 0 ORDER BY producto, id")
            filas = c.fetchall()
            conn.close()

            if filas:
                txt_lines = ["=== INVENTARIO COMPLETO DE CREDENCIALES DISPONIBLES ===", ""]
                prod_actual = ""
                for sid, prod, cred in filas:
                    if prod != prod_actual:
                        prod_actual = prod
                        txt_lines.append(f"\n--- {prod.upper()} ---")
                    txt_lines.append(f"[ID: {sid}]  {cred}")
                txt_lines.append("\nTip: Para borrar una cuenta usa /borrar ID (Ejemplo: /borrar 4)")
                enviar_archivo_txt(
                    message.chat.id,
                    "\n".join(txt_lines),
                    "stock_completo.txt",
                    "🔐 *Archivo TXT con todas tus cuentas disponibles en stock:*"
                )
        return

    # 2. Si escribe "/stock nombre_plataforma"
    busqueda = partes[1]
    producto_encontrado = buscar_producto_nombre(busqueda)
    
    if not producto_encontrado:
        bot.reply_to(message, f"❌ No encontré `{busqueda}` en el catálogo.", reply_markup=boton_volver_menu(), parse_mode="Markdown")
        return

    if producto_encontrado in PRODUCTOS_A_DOMINIO:
        bot.reply_to(
            message,
            f"📦 *{producto_encontrado}*\n🟢 *Modalidad:* Activación directa al correo del cliente.",
            reply_markup=boton_volver_menu(),
            parse_mode="Markdown"
        )
        return

    cant = contar_stock(producto_encontrado)
    color = "🟢" if cant > 0 else "🔴"
    estado_txt = "Disponible para entrega inmediata" if cant > 0 else "Agotado (Sin stock)"
    
    respuesta = (
        f"📦 *Consulta de Stock:*\n\n"
        f"{color} *Plataforma:* {producto_encontrado}\n"
        f"🔢 *Cantidad disponible:* `{cant}`\n"
        f"📌 *Estado:* {estado_txt}"
    )
    bot.reply_to(message, respuesta, reply_markup=boton_volver_menu(), parse_mode="Markdown")
    
    # Si es el Admin, le envía las cuentas de esa plataforma en un archivo .txt
    if es_admin and cant > 0:
        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        c.execute("SELECT id, credenciales FROM stock WHERE producto = ? AND vendido = 0", (producto_encontrado,))
        filas = c.fetchall()
        conn.close()
        
        contenido = f"=== STOCK DISPONIBLE: {producto_encontrado.upper()} ({cant} cuentas) ===\n\n"
        contenido += "\n".join([f"[ID: {f[0]}]  {f[1]}" for f in filas])
        nombre_clean = producto_encontrado.lower().replace(" ", "_")
        enviar_archivo_txt(
            message.chat.id,
            contenido,
            f"stock_{nombre_clean}.txt",
            f"🔐 *Credenciales de {producto_encontrado} en archivo TXT:*"
        )

@bot.message_handler(commands=["cargar"])
def cmd_cargar_menu(message):
    if message.from_user.id != ADMIN_ID:
        return
    limpiar_estados_usuario(ADMIN_ID)

    markup = InlineKeyboardMarkup(row_width=2)
    botones = []
    for prod in CATALOGO.keys():
        if prod in PRODUCTOS_A_DOMINIO:
            continue
        cant = contar_stock(prod)
        color = "🟢" if cant > 0 else "🔴"
        botones.append(InlineKeyboardButton(f"{color} {prod} ({cant})", callback_data=f"cargar_{prod}"))
    markup.add(*botones)
    markup.add(InlineKeyboardButton("🏠 Volver al Menú", callback_data="inicio"))

    bot.reply_to(
        message,
        "📥 *PANEL DE CARGA DE STOCK*\n\n"
        "Elige con un botón a qué plataforma quieres subirle cuentas (`email:pass` o links):",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["historial"])
def cmd_historial_admin(message):
    if message.from_user.id != ADMIN_ID:
        return
    partes = message.text.split(maxsplit=1)
    if len(partes) < 2:
        bot.reply_to(
            message,
            "⚠️ *Uso correcto:*\n`/historial ID_O_@USERNAME`\nEjemplo: `/historial 583920192` o `/historial @cliente`",
            reply_markup=boton_volver_menu(),
            parse_mode="Markdown"
        )
        return

    target_id = buscar_id_por_id_o_username(partes[1])
    if not target_id:
        bot.reply_to(message, "❌ No se encontró a ese usuario en la base de datos.", reply_markup=boton_volver_menu())
        return

    uid, uname, nombre, saldo, total_compras = obtener_datos_usuario(target_id)
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    c.execute("SELECT id, producto, credenciales, precio, fecha FROM historial_compras WHERE user_id = ? ORDER BY id DESC", (uid,))
    compras_lista = c.fetchall()
    conn.close()

    resumen = (
        f"📜 *FICHA E HISTORIAL DEL CLIENTE*\n\n"
        f"👤 *Nombre:* {nombre} ({uname})\n"
        f"🆔 *ID:* `{uid}`\n"
        f"💰 *Saldo actual:* `{saldo:.2f} USDT`\n"
        f"🛍️ *Total de compras:* `{len(compras_lista)}`"
    )
    bot.reply_to(message, resumen, reply_markup=boton_volver_menu(), parse_mode="Markdown")

    if compras_lista:
        lineas_txt = [
            f"=== HISTORIAL DE COMPRAS Y CUENTAS DEL USUARIO {uid} ({uname}) ===",
            f"Nombre: {nombre} | Compras totales: {len(compras_lista)}",
            "===================================================================\n"
        ]
        for cid, prod, cred, prec, fec in compras_lista:
            lineas_txt.append(f"[Compra #{cid}] Fecha: {fec} | Plataforma: {prod} | Pagado: {prec:.2f} USDT")
            lineas_txt.append(f"Credencial / Dato entregado: {cred}")
            lineas_txt.append("-" * 60)

        enviar_archivo_txt(
            message.chat.id,
            "\n".join(lineas_txt),
            f"historial_{uid}.txt",
            f"📂 *Archivo TXT con todas las cuentas compradas por `{uid}`:*"
        )

@bot.message_handler(commands=["noti"])
def cmd_noti_global(message):
    if message.from_user.id != ADMIN_ID:
        return
    limpiar_estados_usuario(ADMIN_ID)
    esperando_noti_global.add(ADMIN_ID)

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Cancelar anuncio global", callback_data="inicio"))
    bot.reply_to(
        message,
        "📢 *MODO NOTIFICACIÓN GLOBAL (A TODOS LOS USUARIOS)* 📢\n\n"
        "👉 **Envía ahora la IMAGEN (con texto en el comentario) o escribe el MENSAJE DE TEXTO** que quieres mandar a todos los miembros del bot (nuevo stock, promociones, avisos, etc.):",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["enviar", "notificar"])
def cmd_notificar_individual(message):
    if message.from_user.id != ADMIN_ID:
        return
    partes = message.text.split(maxsplit=1)
    if len(partes) < 2:
        bot.reply_to(
            message,
            "🔔 *NOTIFICACIÓN INDIVIDUAL A UN CLIENTE:*\n"
            "Escribe `/enviar ID_o_@username`\nEjemplo: `/enviar @cliente`",
            reply_markup=boton_volver_menu(),
            parse_mode="Markdown"
        )
        return

    identificador = partes[1].strip()
    target_id = buscar_id_por_id_o_username(identificador)
    if not target_id:
        bot.reply_to(message, f"❌ No encontré al usuario `{identificador}`.", reply_markup=boton_volver_menu(), parse_mode="Markdown")
        return

    limpiar_estados_usuario(ADMIN_ID)
    estado_notificacion_admin[ADMIN_ID] = target_id
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Cancelar envío", callback_data="inicio"))

    bot.reply_to(
        message,
        f"📨 *Listo para enviar mensaje privado a:* `{identificador}` (ID: `{target_id}`)\n\n"
        f"👉 **Envía ahora la IMAGEN con texto o el MENSAJE** (por ejemplo, avisándole de su invitación de Canva Pro):",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["borrar"])
def cmd_borrar_cuenta(message):
    if message.from_user.id != ADMIN_ID:
        return
    partes = message.text.split()
    if len(partes) != 2 or not partes[1].isdigit():
        bot.reply_to(message, "⚠️ Uso: `/borrar ID_DE_CUENTA`\n*(El ID de cada cuenta aparece en el archivo `.txt` cuando usas `/stock`)*.", parse_mode="Markdown")
        return
    stock_id = int(partes[1])
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    c.execute("DELETE FROM stock WHERE id = ? AND vendido = 0", (stock_id,))
    borrado = c.rowcount
    conn.commit()
    conn.close()
    if borrado:
        bot.reply_to(message, f"🗑️ Cuenta con ID `{stock_id}` eliminada del stock disponible.", reply_markup=boton_volver_menu(), parse_mode="Markdown")
    else:
        bot.reply_to(message, f"❌ No se encontró una cuenta disponible con el ID `{stock_id}`.", reply_markup=boton_volver_menu(), parse_mode="Markdown")

@bot.message_handler(commands=["vaciar"])
def cmd_vaciar_producto(message):
    if message.from_user.id != ADMIN_ID:
        return
    partes = message.text.split(maxsplit=1)
    if len(partes) < 2:
        bot.reply_to(message, "⚠️ Uso: `/vaciar nombre_plataforma`\nEjemplo: `/vaciar gemini`", parse_mode="Markdown")
        return
    prod = buscar_producto_nombre(partes[1])
    if not prod:
        bot.reply_to(message, "❌ No se encontró esa plataforma en el catálogo.")
        return
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    c.execute("DELETE FROM stock WHERE producto = ? AND vendido = 0", (prod,))
    cant = c.rowcount
    conn.commit()
    conn.close()
    bot.reply_to(message, f"🗑️ Se eliminaron `{cant}` cuentas sin vender de *{prod}*.", reply_markup=boton_volver_menu(), parse_mode="Markdown")

@bot.message_handler(commands=["dar_saldo"])
def cmd_dar_saldo(message):
    if message.from_user.id != ADMIN_ID:
        return
    partes = message.text.split()
    if len(partes) != 3:
        bot.reply_to(message, "⚠️ Uso: `/dar_saldo ID_O_@USER MONTO`", parse_mode="Markdown")
        return
    try:
        target_id = buscar_id_por_id_o_username(partes[1])
        monto = float(partes[2])
        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO usuarios (user_id, saldo) VALUES (?, 0.0)", (target_id,))
        c.execute("UPDATE usuarios SET saldo = saldo + ? WHERE user_id = ?", (monto, target_id))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Se sumaron `{monto:.2f} USDT` al usuario `{target_id}`.", reply_markup=boton_volver_menu(), parse_mode="Markdown")
        bot.send_message(target_id, f"🎉 El administrador acreditó `{monto:.2f} USDT` a tu cuenta.", reply_markup=boton_volver_menu(), parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=["quitar_saldo"])
def cmd_quitar_saldo(message):
    if message.from_user.id != ADMIN_ID:
        return
    partes = message.text.split()
    if len(partes) != 3:
        bot.reply_to(message, "⚠️ Uso: `/quitar_saldo ID_O_@USER MONTO`", parse_mode="Markdown")
        return
    try:
        target_id = buscar_id_por_id_o_username(partes[1])
        monto = float(partes[2])
        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        c.execute("UPDATE usuarios SET saldo = MAX(0, saldo - ?) WHERE user_id = ?", (monto, target_id))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Se descontaron `{monto:.2f} USDT` al usuario `{target_id}`.", reply_markup=boton_volver_menu(), parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=["stats"])
def cmd_stats_admin(message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM usuarios")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*), COALESCE(SUM(precio), 0) FROM historial_compras")
    ventas_totales, ingresos_ventas = c.fetchone()
    c.execute("SELECT COUNT(*), COALESCE(SUM(monto), 0) FROM pagos_usados")
    recargas_totales, total_recargado = c.fetchone()
    c.execute("SELECT COUNT(*) FROM stock WHERE vendido = 0")
    stock_disp = c.fetchone()[0]
    conn.close()

    texto = (
        f"📈 *ESTADÍSTICAS GENERALES DE TU TIENDA*\n\n"
        f"👥 *Usuarios registrados:* `{total_users}`\n"
        f"🛍️ *Cuentas vendidas:* `{ventas_totales}`\n"
        f"💵 *Volumen en ventas:* `{ingresos_ventas:.2f} USDT`\n"
        f"💎 *Recargas Binance Pay:* `{recargas_totales}` (`{total_recargado:.2f} USDT`)\n"
        f"📦 *Cuentas en stock ahora:* `{stock_disp}`"
    )
    bot.reply_to(message, texto, reply_markup=boton_volver_menu(), parse_mode="Markdown")

@bot.message_handler(commands=["usuarios"])
def cmd_usuarios_txt(message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect("tienda.db")
    c = conn.cursor()
    c.execute("SELECT user_id, username, nombre, saldo, compras, fecha_registro FROM usuarios ORDER BY fecha_registro DESC")
    filas = c.fetchall()
    conn.close()

    lineas = ["=== LISTA DE USUARIOS REGISTRADOS EN EL BOT ===\n"]
    for uid, uname, nom, sal, comp, fec in filas:
        u_str = f"@{uname}" if uname else "Sin_username"
        lineas.append(f"ID: {uid} | User: {u_str} | Nombre: {nom} | Saldo: {sal:.2f} USDT | Compras: {comp} | Registro: {fec}")
    
    enviar_archivo_txt(message.chat.id, "\n".join(lineas), "usuarios_registrados.txt", f"👥 *Total de usuarios ({len(filas)}) en archivo TXT:*")

# ================= MANEJO DE BOTONES INTERACTIVOS =================
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    user = call.from_user
    user_id = user.id
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    registrar_o_actualizar_usuario(user)

    if call.data == "inicio":
        limpiar_estados_usuario(user_id)
        texto, markup = vista_menu_principal(user)
        try:
            bot.edit_message_text(texto, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            bot.send_message(chat_id, texto, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "panel_admin":
        if user_id != ADMIN_ID:
            return
        bot.edit_message_text(TEXTO_CMDS_ADMIN, chat_id, msg_id, reply_markup=boton_volver_menu(), parse_mode="Markdown")

    elif call.data == "cancelar_accion":
        limpiar_estados_usuario(user_id)
        bot.edit_message_text("❌ Acción cancelada.", chat_id, msg_id, reply_markup=boton_volver_menu())

    elif call.data.startswith("noti_"):
        if user_id != ADMIN_ID:
            return
        target_id = int(call.data.split("_", 1)[1])
        limpiar_estados_usuario(ADMIN_ID)
        estado_notificacion_admin[ADMIN_ID] = target_id
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("❌ Cancelar", callback_data="inicio"))
        bot.send_message(
            chat_id,
            f"📨 *Notificar al cliente ID:* `{target_id}`\n\n"
            f"Envía ahora la **captura/imagen con texto** (o solo texto) avisándole que ya enviaste su invitación:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)

    elif call.data.startswith("cargar_"):
        if user_id != ADMIN_ID:
            return
        producto = call.data.split("_", 1)[1]
        estado_carga_admin[user_id] = producto
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("❌ Cancelar", callback_data="inicio"))
        instrucciones = (
            f"📥 *Subiendo stock para:* `{producto}`\n\n"
            f"Envía ahora en el chat el **correo:contraseña** o el **link de activación**.\n"
            f"*(Puedes pegar varias cuentas, una por cada renglón)*:"
        )
        bot.edit_message_text(instrucciones, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "catalogo":
        limpiar_estados_usuario(user_id)
        texto, markup = vista_catalogo(user_id)
        bot.edit_message_text(texto, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "perfil":
        uid, uname, nombre, saldo, compras = obtener_datos_usuario(user_id)
        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        c.execute("SELECT producto, credenciales, fecha FROM historial_compras WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user_id,))
        ultimas = c.fetchall()
        conn.close()

        historial_txt = ""
        if ultimas:
            historial_txt = "\n\n🧾 *Tus últimas compras:*\n" + "\n".join([f"• *{p}*: `{cr}`" for p, cr, _ in ultimas])

        texto = (
            f"👤 *INFORMACIÓN DE TU CUENTA*\n\n"
            f"🏷️ *Nombre:* {nombre}\n"
            f"🔗 *Username:* `{uname}`\n"
            f"🆔 *ID de Telegram:* `{uid}`\n"
            f"💰 *Saldo Disponible:* `{saldo:.2f} USDT`\n"
            f"🛍️ *Compras Realizadas:* `{compras}`"
            f"{historial_txt}"
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("💎 Recargar Saldo", callback_data="recargar"),
            InlineKeyboardButton("🏠 Volver al Menú Principal", callback_data="inicio")
        )
        bot.edit_message_text(texto, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "soporte":
        limpiar_estados_usuario(user_id)
        texto = (
            f"🛠️ *CENTRO DE SOPORTE Y AYUDA*\n\n"
            f"¿Tienes alguna duda, consulta o algún inconveniente con un pedido o con el bot?\n\n"
            f"• Toca **Escribir reporte aquí** para enviarle un mensaje o captura al administrador.\n"
            f"• O toca **Chat directo** para hablar con soporte."
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("📩 Escribir consulta o reporte aquí", callback_data="abrir_ticket"),
            InlineKeyboardButton("💬 Ir al Chat Directo de Soporte", url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"),
            InlineKeyboardButton("🏠 Volver al Menú Principal", callback_data="inicio")
        )
        bot.edit_message_text(texto, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "abrir_ticket":
        limpiar_estados_usuario(user_id)
        esperando_ticket_soporte.add(user_id)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("❌ Cancelar", callback_data="inicio"))
        bot.edit_message_text(
            "📝 *ESCRIBE TU CONSULTA O REPORTE:*\n\n"
            "Escribe a continuación tu duda o envía una captura del problema:",
            chat_id, msg_id, reply_markup=markup, parse_mode="Markdown"
        )

    elif call.data == "recargar":
        limpiar_estados_usuario(user_id)
        esperando_pago.add(user_id)
        texto = (
            f"💎 *RECARGAR CON BINANCE PAY*\n\n"
            f"1️⃣ Abre Binance y ve a *Pay -> Enviar*.\n"
            f"2️⃣ Envía el monto en *USDT* a este Binance Pay ID:\n"
            f"👉 `{BINANCE_PAY_ID}` *(Toca para copiar)*\n\n"
            f"3️⃣ Una vez hecho el pago, *pega aquí abajo en el chat el ID de Orden (Order ID)* de 18 dígitos de tu comprobante:"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 Volver al Menú Principal", callback_data="inicio"))
        bot.edit_message_text(texto, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data.startswith("buy_"):
        producto = call.data.split("_", 1)[1]
        precio = CATALOGO.get(producto, 999)
        _, uname, nombre, saldo, _ = obtener_datos_usuario(user_id)

        # CASO A: Producto de activación al correo del cliente (ej. Canva Pro)
        if producto in PRODUCTOS_A_DOMINIO:
            if saldo < precio and user_id != ADMIN_ID:
                bot.answer_callback_query(call.id, f"❌ Saldo insuficiente ({saldo:.2f} USDT). Necesitas {precio:.2f} USDT.", show_alert=True)
                return
            
            esperando_correo_dominio[user_id] = producto
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("❌ Cancelar y volver al Catálogo", callback_data="catalogo"))
            bot.edit_message_text(
                f"📧 *ACTIVACIÓN DE {producto.upper()} A TU CORREO*\n\n"
                f"💵 *Costo:* `{precio:.2f} USDT`\n\n"
                f"👉 **Escribe y envía ahora en el chat el correo electrónico** donde deseas recibir la invitación de {producto}:",
                chat_id, msg_id, reply_markup=markup, parse_mode="Markdown"
            )
            return

        # CASO B: Producto normal de entrega inmediata
        disp = contar_stock(producto)
        if disp == 0:
            bot.answer_callback_query(call.id, f"🔴 {producto} está agotado en este momento.", show_alert=True)
            return

        if saldo < precio and user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, f"❌ Saldo insuficiente ({saldo:.2f} USDT). Necesitas {precio:.2f} USDT.", show_alert=True)
            return

        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        c.execute("SELECT id, credenciales FROM stock WHERE producto = ? AND vendido = 0 LIMIT 1", (producto,))
        fila = c.fetchone()

        if not fila:
            conn.close()
            bot.answer_callback_query(call.id, "🔴 Producto agotado.", show_alert=True)
            return

        stock_id, credenciales = fila
        c.execute("UPDATE stock SET vendido = 1 WHERE id = ?", (stock_id,))
        if user_id == ADMIN_ID:
            c.execute("UPDATE usuarios SET saldo = 999999.0, compras = compras + 1 WHERE user_id = ?", (user_id,))
        else:
            c.execute("UPDATE usuarios SET saldo = saldo - ?, compras = compras + 1 WHERE user_id = ?", (precio, user_id))
        
        c.execute("INSERT INTO historial_compras (user_id, producto, credenciales, precio) VALUES (?, ?, ?, ?)",
                  (user_id, producto, credenciales, precio))
        conn.commit()
        conn.close()

        bot.answer_callback_query(call.id, "✅ ¡Compra exitosa!")
        
        recibo = (
            f"🎉 *¡ENTREGA INMEDIATA!* 🎉\n\n"
            f"📦 *Producto:* {producto}\n"
            f"💵 *Pagado:* `{precio:.2f} USDT`\n"
            f"🔑 *Cuenta / Link de activación:*\n`{credenciales}`\n\n"
            f"¡Gracias por tu compra!"
        )
        bot.send_message(chat_id, recibo, reply_markup=boton_volver_menu(), parse_mode="Markdown")
        
        texto, markup = vista_menu_principal(user)
        bot.edit_message_text(texto, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")

# ================= RECEPCIÓN DE IMÁGENES Y MENSAJES =================
@bot.message_handler(content_types=["text", "photo"])
def manejar_entradas(message):
    user = message.from_user
    user_id = user.id
    registrar_o_actualizar_usuario(user)

    # 1. SI EL ADMIN ESTÁ ENVIANDO UNA NOTIFICACIÓN GLOBAL (/noti) A TODOS LOS USUARIOS
    if user_id == ADMIN_ID and ADMIN_ID in esperando_noti_global:
        esperando_noti_global.discard(ADMIN_ID)
        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        c.execute("SELECT user_id FROM usuarios")
        todos = [r[0] for r in c.fetchall()]
        conn.close()

        enviados = 0
        msg_estado = bot.reply_to(message, f"📢 Enviando anuncio global a `{len(todos)}` usuarios...", parse_mode="Markdown")
        
        for uid_destino in todos:
            try:
                if message.content_type == "photo":
                    caption = message.caption or ""
                    bot.send_photo(
                        uid_destino,
                        message.photo[-1].file_id,
                        caption=f"📢 *ANUNCIO OFICIAL — SA SHOP*\n\n{caption}",
                        reply_markup=boton_volver_menu(),
                        parse_mode="Markdown"
                    )
                else:
                    bot.send_message(
                        uid_destino,
                        f"📢 *ANUNCIO OFICIAL — SA SHOP*\n\n{message.text}",
                        reply_markup=boton_volver_menu(),
                        parse_mode="Markdown"
                    )
                enviados += 1
                time.sleep(0.05)
            except Exception:
                pass

        bot.edit_message_text(
            f"✅ *¡Difusión global completada!*\n📨 Entregado con éxito a `{enviados}` de `{len(todos)}` usuarios.",
            message.chat.id, msg_estado.message_id, reply_markup=boton_volver_menu(), parse_mode="Markdown"
        )
        return

    # 2. SI EL ADMIN ESTÁ ENVIANDO UNA NOTIFICACIÓN INDIVIDUAL (/enviar o botón de Canva)
    if user_id == ADMIN_ID and ADMIN_ID in estado_notificacion_admin:
        target_id = estado_notificacion_admin.pop(ADMIN_ID)
        try:
            if message.content_type == "photo":
                file_id = message.photo[-1].file_id
                caption = message.caption or "✅ ¡Tu invitación ha sido enviada! Revisa tu correo y acéptala."
                bot.send_photo(
                    target_id,
                    file_id,
                    caption=f"🔔 *NOTIFICACIÓN DEL ADMINISTRADOR:*\n\n{caption}",
                    reply_markup=boton_volver_menu(),
                    parse_mode="Markdown"
                )
            else:
                bot.send_message(
                    target_id,
                    f"🔔 *NOTIFICACIÓN DEL ADMINISTRADOR:*\n\n{message.text}",
                    reply_markup=boton_volver_menu(),
                    parse_mode="Markdown"
                )
            bot.reply_to(message, f"✅ Notificación enviada al cliente `{target_id}`.", reply_markup=boton_volver_menu(), parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Error al notificar a `{target_id}`: {e}", reply_markup=boton_volver_menu(), parse_mode="Markdown")
        return

    # 3. SI UN CLIENTE ESTÁ ENVIANDO UN REPORTE A SOPORTE
    if user_id in esperando_ticket_soporte:
        esperando_ticket_soporte.discard(user_id)
        uid, uname, nombre, saldo, _ = obtener_datos_usuario(user_id)
        
        encabezado_admin = (
            f"🆘 *NUEVO MENSAJE DE SOPORTE*\n\n"
            f"👤 *Cliente:* {nombre} ({uname})\n"
            f"🆔 *ID:* `{uid}`\n"
            f"💰 *Saldo:* `{saldo:.2f} USDT`"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📨 Responder al Cliente", callback_data=f"noti_{uid}"))

        if message.content_type == "photo":
            comentario = message.caption or "(Sin texto adjunto)"
            bot.send_photo(
                ADMIN_ID,
                message.photo[-1].file_id,
                caption=f"{encabezado_admin}\n\n💬 *Mensaje:* {comentario}",
                reply_markup=markup,
                parse_mode="Markdown"
            )
        else:
            bot.send_message(
                ADMIN_ID,
                f"{encabezado_admin}\n\n💬 *Mensaje:*\n{message.text}",
                reply_markup=markup,
                parse_mode="Markdown"
            )

        bot.reply_to(
            message,
            "✅ *Tu mensaje fue enviado a Soporte.*\nEn breve el administrador te responderá por aquí.",
            reply_markup=boton_volver_menu(),
            parse_mode="Markdown"
        )
        return

    if message.content_type != "text":
        return

    texto = message.text.strip()

    # 4. SI EL ADMIN ESTÁ CARGANDO STOCK CON /cargar
    if user_id == ADMIN_ID and user_id in estado_carga_admin:
        producto = estado_carga_admin.pop(user_id)
        lineas = [l.strip() for l in texto.split("\n") if l.strip()]
        
        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        for credencial in lineas:
            c.execute("INSERT INTO stock (producto, credenciales) VALUES (?, ?)", (producto, credencial))
        conn.commit()
        conn.close()

        nuevo_total = contar_stock(producto)
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("📥 Cargar otro producto", callback_data="catalogo"),
            InlineKeyboardButton("🏠 Ir al Menú Principal", callback_data="inicio")
        )
        bot.reply_to(
            message,
            f"✅ *¡Carga exitosa!*\n\n"
            f"📦 *Plataforma:* {producto}\n"
            f"➕ *Agregadas:* `{len(lineas)}`\n"
            f"🟢 *Stock total disponible:* `{nuevo_total}`",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return

    # 5. SI EL CLIENTE ESTÁ ENVIANDO SU CORREO PARA CANVA PRO
    if user_id in esperando_correo_dominio:
        if "@" not in texto or "." not in texto or " " in texto:
            bot.reply_to(message, "⚠️ Correo inválido. Escribe bien tu correo (ejemplo: `micorreo@gmail.com`):", parse_mode="Markdown")
            return

        producto = esperando_correo_dominio.pop(user_id)
        precio = CATALOGO.get(producto, 999)
        uid, uname, nombre, saldo, _ = obtener_datos_usuario(user_id)

        if saldo < precio and user_id != ADMIN_ID:
            bot.reply_to(message, "❌ No tienes saldo suficiente.", reply_markup=boton_volver_menu())
            return

        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        if user_id == ADMIN_ID:
            c.execute("UPDATE usuarios SET saldo = 999999.0, compras = compras + 1 WHERE user_id = ?", (user_id,))
        else:
            c.execute("UPDATE usuarios SET saldo = saldo - ?, compras = compras + 1 WHERE user_id = ?", (precio, user_id))
        
        c.execute("INSERT INTO historial_compras (user_id, producto, credenciales, precio) VALUES (?, ?, ?, ?)",
                  (user_id, producto, f"Activación a correo: {texto}", precio))
        conn.commit()
        conn.close()

        bot.reply_to(
            message,
            f"✅ *¡PEDIDO DE {producto.upper()} REGISTRADO!* ✅\n\n"
            f"📧 *Correo registrado:* `{texto}`\n"
            f"💵 *Pagado:* `{precio:.2f} USDT`\n\n"
            f"⏳ En breve te enviaremos la invitación a tu correo y recibirás una notificación aquí mismo cuando esté lista.",
            reply_markup=boton_volver_menu(),
            parse_mode="Markdown"
        )

        alerta_admin = (
            f"🔔 *¡NUEVA COMPRA A DOMINIO ({producto})!* 🔔\n\n"
            f"👤 *Cliente:* {nombre} ({uname})\n"
            f"🆔 *ID:* `{uid}`\n"
            f"💵 *Pagado:* `{precio:.2f} USDT`\n"
            f"📧 *Correo del cliente:*\n👉 `{texto}`"
        )
        markup_admin = InlineKeyboardMarkup()
        markup_admin.add(InlineKeyboardButton("📸 Enviar Notificación (Foto + Texto) al Cliente", callback_data=f"noti_{uid}"))
        bot.send_message(ADMIN_ID, alerta_admin, reply_markup=markup_admin, parse_mode="Markdown")
        return

    # 6. SI UN CLIENTE ESTÁ ENVIANDO SU ORDER ID DE BINANCE PAY
    if user_id in esperando_pago or (texto.isdigit() and len(texto) >= 15):
        order_id = texto
        conn = sqlite3.connect("tienda.db")
        c = conn.cursor()
        c.execute("SELECT order_id FROM pagos_usados WHERE order_id = ?", (order_id,))
        if c.fetchone():
            conn.close()
            bot.reply_to(message, "❌ Este ID de transacción ya fue utilizado anteriormente.", reply_markup=boton_volver_menu())
            return

        msg = bot.reply_to(message, "⏳ Verificando tu pago con Binance Pay...")
        exito, resultado = verificar_pago_binance(order_id)

        if exito:
            monto = resultado
            c.execute("INSERT INTO pagos_usados (order_id, user_id, monto) VALUES (?, ?, ?)", (order_id, user_id, monto))
            if user_id != ADMIN_ID:
                c.execute("UPDATE usuarios SET saldo = saldo + ? WHERE user_id = ?", (monto, user_id))
            conn.commit()
            conn.close()
            esperando_pago.discard(user_id)

            _, _, _, nuevo_saldo, _ = obtener_datos_usuario(user_id)
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("🛒 Ir al Catálogo", callback_data="catalogo"),
                InlineKeyboardButton("🏠 Menú Principal", callback_data="inicio")
            )
            bot.edit_message_text(
                f"✅ *¡PAGO VERIFICADO!* ✅\n\n"
                f"➕ *Recargado:* `{monto:.2f} USDT`\n"
                f"💰 *Tu nuevo saldo:* `{nuevo_saldo:.2f} USDT`",
                message.chat.id, msg.message_id, reply_markup=markup, parse_mode="Markdown"
            )
        else:
            conn.close()
            bot.edit_message_text(
                f"❌ *No se pudo validar el código:* `{order_id}`\n"
                f"Motivo: {resultado}\n\n"
                f"Asegúrate de pegar el *Order ID* numérico de tu comprobante de Binance Pay.",
                message.chat.id, msg.message_id, reply_markup=boton_volver_menu(), parse_mode="Markdown"
            )
        return

    # 7. Si escribe cualquier otra cosa suelta, le muestra el botón para abrir el menú
    bot.reply_to(
        message,
        "💡 Usa `/menu` o toca el botón de abajo para abrir el panel principal:",
        reply_markup=boton_volver_menu(),
        parse_mode="Markdown"
    )

print("🚀 Bot SA SHOP Pro activo: Admin 999999 USDT + TXT en /stock y /historial + /noti global...")
bot.infinity_polling()