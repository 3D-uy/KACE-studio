# KACE Studio — Roadmap

🌐 [English](../../ROADMAP.md) · [Español](../../docs/es/ROADMAP.md) · [Português](../../docs/pt/ROADMAP.md)

Prioridades de este código, sin fechas prometidas ni afirmaciones de calificación completada. Las guías de release siguen siendo la autoridad para los gates; este roadmap no modifica versiones, hashes, targets de firmware ni pins.

## 📍 Disponible en código

Existen imagen guiada, provisión del primer arranque, Discovery, SSH/SFTP, progreso de bootstrap y recuperación del checkpoint KACE. Python conserva la autoridad; el frontend debe respetar validaciones, identidades de operación y resultados terminales.

## 🧭 Prioridades

| Prioridad | Resultado requerido | Referencia |
| --- | --- | --- |
| 1 · Validación de interfaz | Verificar selectores/iconos de Imager, teclado y foco, credenciales, SFTP persistente y recuperación tanto desde código como en WebView2 nativo. | [Desarrollo (EN)](../../docs/DEVELOPMENT.md) |
| 2 · Evidencia de código y paquete | Conciliar la matriz soportada de SO/Python; validar después bootstrap de código/empaquetado, assets web exactos y smoke del renderer empaquetado. | [Checklist (EN)](../../RELEASE_CHECKLIST.md) |
| 3 · Calificación controlada | Probar identidad real del destino, elevación, lectura de verificación, expulsión, primer arranque, SSH/SFTP y finalización KACE bajo control del operador. | [Provisión (EN)](../../docs/IMAGE_PROVISIONING.md) |
| 4 · Distribución y mantenimiento | Resolver bloqueos de identidad de release; obtener artefactos firmados y reproducidos independientemente antes de afirmar esas propiedades. Mantener paridad de idiomas y evidencia de regresión. | [Checklist (EN)](../../RELEASE_CHECKLIST.md) |

## Límites de alcance

Pruebas en Linux no equivalen a soporte del escritorio/writer en Linux. Los nuevos targets de firmware y decisiones de seguridad de la impresora corresponden a KACE. Plataformas o flujos adicionales requieren alcance y validación separados.

[Volver al README](README.md)
