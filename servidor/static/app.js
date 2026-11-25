// PWA del asistente. Solo pinta lo que devuelve la API: que ve cada rol lo decide el servidor.
"use strict";

const EJEMPLOS = {
  comercial: [
    ["Chapa 316 agotada en Madrid", "Necesito 2.000 kg de chapa 316 2B de 2 mm, 1500 x 3000, en el almacén de Madrid."],
    ["Barandilla junto al mar", "Un cliente va a fabricar una barandilla exterior en primera línea de playa. No sabe qué grado pedir. ¿Qué tubo le ofrezco?"],
    ["Horno industrial", "Chapa de 3 mm para el interior de un horno industrial que trabaja a unos 1.000 °C. ¿Qué tenemos?"],
    ["Stock parado: 430 BA", "Un fabricante de electrodomésticos busca chapa 430 BA fina, entre 0,5 y 0,8 mm. ¿Qué nos interesa más venderle?"],
  ],
  cliente: [
    ["Chapa 316 en Madrid", "Necesito 2.000 kg de chapa 316 2B de 2 mm, 1500 x 3000, en el almacén de Madrid."],
    ["Barandilla junto al mar", "Voy a fabricar una barandilla exterior en primera línea de playa. ¿Qué tubo me recomendáis?"],
    ["Chapa 430 BA fina", "¿Tenéis chapa 430 BA entre 0,5 y 0,8 mm?"],
  ],
};

const PESTANAS = {
  comercial: [["chat", "Chat"], ["ofertas", "Ofertas"], ["avisos", "Demanda sin stock"], ["notificaciones", "Notificaciones"]],
  cliente: [["chat", "Chat"], ["avisos", "Mis avisos"], ["notificaciones", "Notificaciones"]],
};

const NOMBRES_HERRAMIENTA = {
  buscar_stock: "Buscar stock", ficha_tecnica: "Ficha técnica",
  alternativas: "Alternativas", prioridad_comercial: "Prioridad comercial",
};

const $ = (s) => document.querySelector(s);
let perfil = null;
let conversacionActual = null;   // id de la conversacion abierta; null = nueva
let ultimaNotificacion = 0;
let sondeo = null;

// --- Utilidades -----------------------------------------------------------------

function escapar(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

// Markdown minimo y seguro: se escapa todo y luego se reconocen **negrita**, listas y parrafos.
function formatear(texto) {
  const bloques = escapar(texto).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").split(/\n{2,}/);
  return bloques.map((b) => {
    const lineas = b.split("\n");
    if (lineas.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l))) {
      return "<ul>" + lineas.map((l) => `<li>${l.replace(/^\s*([-*•]|\d+\.)\s+/, "")}</li>`).join("") + "</ul>";
    }
    return `<p>${lineas.join("<br>")}</p>`;
  }).join("");
}

const numero = (v, d = 0) => Number(v).toLocaleString("es-ES", {maximumFractionDigits: d});

function avisar(texto) {
  const caja = $("#aviso-flotante");
  caja.textContent = texto;
  caja.hidden = false;
  clearTimeout(avisar.t);
  avisar.t = setTimeout(() => (caja.hidden = true), 3500);
}

async function api(ruta, opciones = {}) {
  const r = await fetch(ruta, {
    credentials: "same-origin",
    headers: opciones.cuerpo ? {"content-type": "application/json"} : {},
    method: opciones.metodo || (opciones.cuerpo ? "POST" : "GET"),
    body: opciones.cuerpo ? JSON.stringify(opciones.cuerpo) : undefined,
  });
  const datos = await r.json().catch(() => ({}));
  if (r.status === 401 && ruta !== "/api/login") { mostrarLogin(); }
  if (!r.ok) {
    const detalle = typeof datos.detail === "string" ? datos.detail : "No se ha podido completar la operación.";
    throw new Error(detalle);
  }
  return datos;
}

function guardar(clave, valor) { try { localStorage.setItem(clave, valor); } catch (_) {} }
function leer(clave) { try { return localStorage.getItem(clave); } catch (_) { return null; } }

// --- Sesion ---------------------------------------------------------------------

function mostrarLogin() {
  perfil = null;
  clearInterval(sondeo);
  $("#pantalla-app").hidden = true;
  $("#barra-usuario").hidden = true;
  $("#pantalla-login").hidden = false;
}

function entrar(p) {
  perfil = p;
  $("#pantalla-login").hidden = true;
  $("#pantalla-app").hidden = false;
  $("#barra-usuario").hidden = false;
  $("#etiqueta-rol").textContent = `${p.usuario} · ${p.rol}`;
  pintarPestanas();
  pintarEjemplos();
  nuevaConversacion(false);
  cargarHistorial();
  ultimaNotificacion = Number(leer(`ultima-${p.usuario}`) || 0);
  revisarNotificaciones(true);
  clearInterval(sondeo);
  sondeo = setInterval(() => revisarNotificaciones(false), 15000);
}

$("#form-login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("#error-login").hidden = true;
  try {
    entrar(await api("/api/login", {cuerpo: {usuario: f.get("usuario"), clave: f.get("clave")}}));
    e.target.reset();
  } catch (err) {
    $("#error-login").textContent = err.message;
    $("#error-login").hidden = false;
  }
});

$("#btn-salir").addEventListener("click", async () => {
  await api("/api/logout", {metodo: "POST"}).catch(() => {});
  mostrarLogin();
});

// --- Pestanas -------------------------------------------------------------------

function pintarPestanas() {
  const nav = $("#pestanas");
  nav.innerHTML = "";
  for (const [id, nombre] of PESTANAS[perfil.rol]) {
    const b = document.createElement("button");
    b.textContent = nombre;
    b.dataset.vista = id;
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => abrirVista(id));
    nav.append(b);
  }
  abrirVista("chat");
}

function abrirVista(id) {
  for (const b of document.querySelectorAll("#pestanas button")) b.setAttribute("aria-selected", b.dataset.vista === id);
  for (const v of document.querySelectorAll(".vista")) v.hidden = v.id !== `vista-${id}`;
  if (id === "ofertas") cargarOfertas();
  if (id === "avisos") cargarAvisos();
  if (id === "notificaciones") cargarNotificaciones();
}

// --- Chat -----------------------------------------------------------------------

function pintarEjemplos() {
  const caja = $("#ejemplos");
  caja.innerHTML = "";
  for (const [titulo, texto] of EJEMPLOS[perfil.rol]) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = titulo;
    b.addEventListener("click", () => preguntar(texto));
    caja.append(b);
  }
}

// --- Historial de conversaciones ------------------------------------------------
// El servidor guarda cada conversacion; aqui solo se lista, se abre y se borra.

function mostrarChatVacio(vacio) {
  $("#ejemplos").hidden = !vacio;
}

function marcarActual() {
  for (const fila of document.querySelectorAll(".conversacion")) {
    fila.setAttribute("aria-current", Number(fila.dataset.id) === conversacionActual);
  }
}

async function nuevaConversacion(avisarServidor = true) {
  conversacionActual = null;
  $("#mensajes").innerHTML = "";
  $("#titulo-conversacion").textContent = "Nueva conversación";
  mostrarChatVacio(true);
  marcarActual();
  cerrarHistorial();
  if (avisarServidor) await api("/api/conversacion/nueva", {metodo: "POST"}).catch(() => {});
  $("#pregunta").focus();
}

function grupoFecha(iso) {
  const dias = Math.floor((Date.now() - new Date(iso)) / 86400000);
  if (dias < 1) return "Hoy";
  if (dias < 2) return "Ayer";
  if (dias < 7) return "Últimos 7 días";
  if (dias < 30) return "Últimos 30 días";
  return "Anteriores";
}

async function cargarHistorial() {
  const lista = $("#lista-conversaciones");
  let conversaciones;
  try { conversaciones = await api("/api/conversaciones"); }
  catch (err) { lista.innerHTML = `<p class="error pequeno">${escapar(err.message)}</p>`; return; }
  lista.innerHTML = conversaciones.length ? "" : '<p class="tenue pequeno">Tus conversaciones aparecerán aquí.</p>';
  let grupo = null;
  for (const c of conversaciones) {
    const g = grupoFecha(c.actualizada);
    if (g !== grupo) {
      grupo = g;
      const cab = document.createElement("div");
      cab.className = "grupo-fecha";
      cab.textContent = g;
      lista.append(cab);
    }
    lista.append(filaConversacion(c));
  }
  marcarActual();
}

function filaConversacion(c) {
  const fila = document.createElement("div");
  fila.className = "conversacion";
  fila.dataset.id = c.id;
  const abrir = document.createElement("button");
  abrir.type = "button";
  abrir.className = "abrir";
  abrir.textContent = c.titulo;
  abrir.title = c.titulo;
  abrir.addEventListener("click", () => abrirConversacion(c.id));
  // Borrar pide un segundo toque en vez de un dialogo del navegador.
  const borrar = document.createElement("button");
  borrar.type = "button";
  borrar.className = "borrar";
  borrar.textContent = "✕";
  borrar.setAttribute("aria-label", `Borrar «${c.titulo}»`);
  borrar.addEventListener("click", async () => {
    if (!borrar.classList.contains("confirmar")) {
      borrar.classList.add("confirmar");
      borrar.textContent = "¿Borrar?";
      setTimeout(() => { borrar.classList.remove("confirmar"); borrar.textContent = "✕"; }, 3000);
      return;
    }
    try {
      await api(`/api/conversaciones/${c.id}`, {metodo: "DELETE"});
      if (conversacionActual === c.id) nuevaConversacion(false);
      cargarHistorial();
    } catch (err) { avisar(err.message); }
  });
  fila.append(abrir, borrar);
  return fila;
}

async function abrirConversacion(id) {
  cerrarHistorial();
  if ($("#btn-enviar").disabled) { avisar("Espera a que termine la respuesta anterior."); return; }
  try {
    const c = await api(`/api/conversaciones/${id}`);
    conversacionActual = c.id;
    $("#titulo-conversacion").textContent = c.titulo;
    $("#mensajes").innerHTML = "";
    for (const t of c.turnos) {
      anadir("usuario", escapar(t.pregunta));
      pintarRespuesta(t.respuesta);
    }
    mostrarChatVacio(!c.turnos.length);
    marcarActual();
  } catch (err) {
    avisar(err.message);
    cargarHistorial();
  }
}

function abrirHistorial() {
  $("#historial").classList.add("abierto");
  $("#velo").hidden = false;
  $("#btn-historial").setAttribute("aria-expanded", "true");
}

function cerrarHistorial() {
  $("#historial").classList.remove("abierto");
  $("#velo").hidden = true;
  $("#btn-historial").setAttribute("aria-expanded", "false");
}

$("#btn-nueva").addEventListener("click", () => nuevaConversacion());
$("#btn-historial").addEventListener("click", abrirHistorial);
$("#velo").addEventListener("click", cerrarHistorial);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarHistorial(); });

function anadir(clase, html) {
  const div = document.createElement("div");
  div.className = `mensaje ${clase}`;
  div.innerHTML = html;
  $("#mensajes").append(div);
  div.scrollIntoView({behavior: "smooth", block: "end"});
  return div;
}

async function preguntar(texto) {
  texto = texto.trim();
  if (!texto) return;
  const boton = $("#btn-enviar");
  boton.disabled = true;
  mostrarChatVacio(false);
  anadir("usuario", escapar(texto));
  const pensando = anadir("asistente pensando", "Consultando el inventario…");
  try {
    const r = await api("/api/preguntar", {cuerpo: {pregunta: texto, conversacion: conversacionActual}});
    pensando.remove();
    pintarRespuesta(r);
    conversacionActual = r.conversacion;
    cargarHistorial().then(() => {
      const fila = document.querySelector(`.conversacion[data-id="${r.conversacion}"] .abrir`);
      if (fila) $("#titulo-conversacion").textContent = fila.textContent;
    });
  } catch (err) {
    pensando.remove();
    anadir("error", escapar(err.message));
  } finally {
    boton.disabled = false;
  }
}

function pintarRespuesta(r) {
  const div = anadir("asistente", `<div class="texto">${formatear(r.texto)}</div>`);
  const sello = document.createElement("div");
  sello.className = `sello ${r.verificada ? "ok" : "alerta"}`;
  sello.textContent = r.verificada
    ? "✓ Productos comprobados contra el inventario"
    : "⚠ Respuesta del modelo sustituida por los datos del inventario";
  div.append(sello);

  if (r.productos.length) {
    const lista = document.createElement("div");
    lista.className = "productos";
    for (const p of r.productos) lista.append(tarjetaProducto(p));
    div.append(lista);
  }

  if (r.sin_stock.length) {
    const caja = document.createElement("div");
    caja.className = "sin-stock";
    for (const s of r.sin_stock) {
      const b = document.createElement("button");
      b.className = "secundario";
      b.textContent = `Avisarme cuando haya ${s.descripcion}`;
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const a = await api("/api/avisos", {cuerpo: {busqueda: s.busqueda}});
          b.textContent = `Aviso guardado: ${a.descripcion}`;
          pedirPermisoNotificaciones();
        } catch (err) { b.disabled = false; avisar(err.message); }
      });
      caja.append(b);
    }
    div.append(caja);
  }

  // Solo llega del servidor si el rol puede verlo.
  if (r.razonamiento) div.append(panelRazonamiento(r.razonamiento));
}

function tarjetaProducto(p) {
  const t = document.createElement("div");
  t.className = "producto";
  const medidas = p.formato === "barra" || p.formato === "tubo"
    ? `${numero(p.espesor_mm, 2)} × Ø${numero(p.ancho_mm, 1)} mm`
    : `${numero(p.espesor_mm, 2)} × ${numero(p.ancho_mm)} mm`;
  let extra = "";
  if (p.precio_eur_kg !== undefined) extra += ` · ${numero(p.precio_eur_kg, 2)} €/kg`;
  if (p.rotacion_dias !== undefined) extra += ` · ${p.rotacion_dias} días en almacén`;
  t.innerHTML = `
    <div class="id">${escapar(p.id)}</div>
    <div>${escapar(p.grado)} ${escapar(p.formato)} ${escapar(p.acabado)}</div>
    <div class="datos">${medidas}${p.norma ? " · " + escapar(p.norma) : ""}</div>
    <div class="stock"><b>${numero(p.stock_kg)} kg</b> en ${escapar(p.almacen)}</div>
    ${extra ? `<div class="datos">${escapar(extra.slice(3))}</div>` : ""}`;
  if (perfil.rol === "cliente") t.append(botonOferta(p));
  return t;
}

function botonOferta(p) {
  const caja = document.createElement("div");
  const b = document.createElement("button");
  b.className = "secundario";
  b.textContent = "Pedir oferta";
  b.addEventListener("click", () => {
    b.hidden = true;
    const f = document.createElement("form");
    f.innerHTML = `
      <input name="cantidad" type="number" min="1" step="any" inputmode="decimal" placeholder="Cantidad (kg)">
      <input name="comentario" maxlength="2000" placeholder="Comentario (opcional)">
      <button class="primario" type="submit">Enviar petición</button>`;
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const datos = new FormData(f);
      const cantidad = Number(datos.get("cantidad")) || null;
      try {
        await api("/api/ofertas", {cuerpo: {producto_id: p.id, cantidad_kg: cantidad, comentario: datos.get("comentario")}});
        caja.innerHTML = `<p class="sello ok">✓ Petición enviada. Un comercial te contestará.</p>`;
      } catch (err) { avisar(err.message); }
    });
    caja.append(f);
  });
  caja.append(b);
  return caja;
}

function panelRazonamiento(r) {
  const d = document.createElement("details");
  d.className = "razonamiento";
  const pasos = r.traza.map((p, i) => `
    <div class="paso ${p.error ? "fallo" : ""}">
      <b>${i + 1}. ${escapar(NOMBRES_HERRAMIENTA[p.herramienta] || p.herramienta)}</b> · ${p.ms} ms
      <pre>${escapar(JSON.stringify(p.argumentos))}</pre>
      <details><summary>Resultado</summary><pre>${escapar(JSON.stringify(p.resultado, null, 2))}</pre></details>
    </div>`).join("");
  const rechazados = r.rechazados.length
    ? `<p class="error">El verificador rechazó: ${escapar(r.rechazados.join(", "))}${r.reintentada ? " (el modelo tuvo que reescribir)" : ""}</p>`
    : `<p class="tenue">Verificador: todos los productos citados salen de las herramientas.</p>`;
  d.innerHTML = `
    <summary>Ver razonamiento (${r.traza.length} consultas, ${numero(r.ms / 1000, 1)} s)</summary>
    ${pasos || '<p class="tenue">Sin consultas al inventario.</p>'}
    ${rechazados}
    <p class="tenue pequeno">${r.llamadas_llm} llamadas al modelo · ${numero(r.tokens_entrada)} tokens de entrada · ${numero(r.tokens_salida)} de salida</p>`;
  return d;
}

const caja = $("#pregunta");
$("#form-pregunta").addEventListener("submit", (e) => {
  e.preventDefault();
  const texto = caja.value;
  caja.value = "";
  caja.style.height = "";
  preguntar(texto);
});
caja.addEventListener("keydown", (e) => {
  // Enter envia; Mayus+Enter hace salto de linea (para pegar correos).
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#form-pregunta").requestSubmit(); }
});
caja.addEventListener("input", () => { caja.style.height = "auto"; caja.style.height = caja.scrollHeight + "px"; });

// --- Ofertas y avisos -----------------------------------------------------------

const fecha = (iso) => new Date(iso).toLocaleString("es-ES", {dateStyle: "short", timeStyle: "short"});

async function cargarOfertas() {
  const lista = $("#lista-ofertas");
  try {
    const ofertas = await api("/api/ofertas");
    lista.innerHTML = ofertas.length ? ofertas.map((o) => `
      <div class="fila">
        <div><b>${escapar(o.producto_id)}</b>${o.cantidad_kg ? ` · ${numero(o.cantidad_kg)} kg` : ""}</div>
        ${o.comentario ? `<div>${escapar(o.comentario)}</div>` : ""}
        <div class="meta">${escapar(o.usuario)} · ${fecha(o.creada)} · ${escapar(o.estado)}</div>
      </div>`).join("") : '<p class="tenue">Todavía no hay peticiones.</p>';
  } catch (err) { lista.innerHTML = `<p class="error">${escapar(err.message)}</p>`; }
}

async function cargarAvisos() {
  const esComercial = perfil.rol === "comercial";
  $("#btn-comprobar").hidden = !esComercial;
  $("#nota-avisos").textContent = esComercial
    ? "Materiales que alguien pidió y no había. Cada aviso es demanda no atendida."
    : "Te avisaremos cuando vuelva a haber stock de estos materiales.";
  const lista = $("#lista-avisos");
  try {
    const avisos = await api("/api/avisos");
    lista.innerHTML = avisos.length ? avisos.map((a) => `
      <div class="fila">
        <div><b>${escapar(a.descripcion)}</b></div>
        <div class="meta">${esComercial ? escapar(a.usuario) + " · " : ""}${fecha(a.creado)} ·
          ${a.cumplido ? "con stock desde " + fecha(a.cumplido) : "pendiente"}</div>
      </div>`).join("") : '<p class="tenue">No hay avisos.</p>';
  } catch (err) { lista.innerHTML = `<p class="error">${escapar(err.message)}</p>`; }
}

$("#btn-comprobar").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    const r = await api("/api/avisos/comprobar", {metodo: "POST"});
    avisar(r.cumplidos.length ? `${r.cumplidos.length} avisos con stock: se ha notificado a sus usuarios.` : "Ningún aviso tiene stock todavía.");
    cargarAvisos();
  } catch (err) { avisar(err.message); } finally { e.target.disabled = false; }
});

// --- Notificaciones -------------------------------------------------------------
// La app pregunta cada 15 s si hay notificaciones nuevas y las enseña como
// notificacion del sistema. Con la app cerrada haria falta Web Push (pendiente).

function pedirPermisoNotificaciones() {
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
}

$("#btn-avisos-sistema").addEventListener("click", () => {
  pedirPermisoNotificaciones();
  $("#contador").hidden = true;
  abrirVista("notificaciones");
});

async function mostrarSistema(n) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const reg = await navigator.serviceWorker?.getRegistration();
  if (reg) reg.showNotification(n.titulo, {body: n.texto, icon: "/iconos/icono-192.png", tag: `n${n.id}`});
  else new Notification(n.titulo, {body: n.texto});
}

async function revisarNotificaciones(primera) {
  if (!perfil) return;
  try {
    const nuevas = await api(`/api/notificaciones?desde=${ultimaNotificacion}`);
    if (!nuevas.length) return;
    ultimaNotificacion = nuevas[nuevas.length - 1].id;
    guardar(`ultima-${perfil.usuario}`, ultimaNotificacion);
    const contador = $("#contador");
    contador.textContent = Number(contador.hidden ? 0 : contador.textContent) + nuevas.length;
    contador.hidden = false;
    if (!primera) for (const n of nuevas) { mostrarSistema(n); avisar(`${n.titulo}: ${n.texto}`); }
  } catch (_) { /* sin red: se reintenta en el siguiente sondeo */ }
}

async function cargarNotificaciones() {
  const lista = $("#lista-notificaciones");
  try {
    const todas = await api("/api/notificaciones?desde=0");
    lista.innerHTML = todas.length ? todas.reverse().map((n) => `
      <div class="fila"><div><b>${escapar(n.titulo)}</b></div><div>${escapar(n.texto)}</div>
      <div class="meta">${fecha(n.creada)}</div></div>`).join("") : '<p class="tenue">Sin notificaciones.</p>';
  } catch (err) { lista.innerHTML = `<p class="error">${escapar(err.message)}</p>`; }
}

// --- Arranque -------------------------------------------------------------------

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

api("/api/yo").then(entrar).catch(mostrarLogin);
