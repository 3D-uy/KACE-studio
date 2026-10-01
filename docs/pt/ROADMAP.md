# KACE Studio — Roadmap

🌐 [English](../../ROADMAP.md) · [Español](../../docs/es/ROADMAP.md) · [Português](../../docs/pt/ROADMAP.md)

Prioridades deste código, sem datas prometidas ou afirmações de qualificação concluída. Os guias de release continuam sendo a autoridade dos gates; este roadmap não altera versões, hashes, targets de firmware nem pins.

## 📍 Disponível no código

Existem imagem guiada, primeira inicialização, Discovery, SSH/SFTP, progresso do bootstrap e recuperação do checkpoint KACE. Python mantém a autoridade; o frontend deve respeitar validações, identidades de operação e resultados terminais.

## 🧭 Prioridades

| Prioridade | Resultado exigido | Referência |
| --- | --- | --- |
| 1 · Validação da interface | Verificar seletores/ícones do Imager, teclado e foco, credenciais, SFTP persistente e recuperação tanto no código quanto no WebView2 nativo. | [Desenvolvimento (EN)](../../docs/DEVELOPMENT.md) |
| 2 · Evidência de código e pacote | Conciliar a matriz suportada de SO/Python; validar depois bootstrap de código/empacotado, assets web exatos e smoke do renderer empacotado. | [Checklist (EN)](../../RELEASE_CHECKLIST.md) |
| 3 · Qualificação controlada | Testar identidade real do destino, elevação, leitura de verificação, ejeção, primeira inicialização, SSH/SFTP e conclusão KACE sob controle do operador. | [Provisionamento (EN)](../../docs/IMAGE_PROVISIONING.md) |
| 4 · Distribuição e manutenção | Distribuir o candidato atual sem Authenticode, mantendo SHA-256, manifesto de release, atestação do rebuild independente e commit exato de origem. Adiar a assinatura para uma release assinada futura, preservando seu gate. Manter paridade de idiomas e evidência de regressão. | [Checklist (EN)](../../RELEASE_CHECKLIST.md) |

## Limites de escopo

Testes no Linux não equivalem a suporte ao desktop/writer no Linux. Novos targets de firmware e decisões de segurança da impressora pertencem ao KACE. Plataformas ou fluxos adicionais exigem escopo e validação separados.

[Voltar ao README](README.md)
