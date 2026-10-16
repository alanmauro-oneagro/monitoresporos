// Rosa dos ventos (Chart.js 'polarArea') -- compartilhada entre a aba
// Graficos e a tela de Recomendacoes. Cada gomo e' uma das 8 direcoes,
// do tamanho da frequencia (%) das leituras vindas daquela direcao.
(function () {
    // Marca d'agua de bussola no fundo (vetor, azul OneAgro #0a1f44), em
    // `beforeDraw` -- antes dos gomos de verdade.
    function desenharPontaBussola(ctx, cx, cy, anguloGraus, comprimento, largura) {
        var ang = anguloGraus * Math.PI / 180;
        var dirx = Math.sin(ang), diry = -Math.cos(ang);
        var perpx = -diry, perpy = dirx;
        ctx.beginPath();
        ctx.moveTo(cx + dirx * comprimento, cy + diry * comprimento);
        ctx.lineTo(cx + perpx * largura, cy + perpy * largura);
        ctx.lineTo(cx, cy);
        ctx.lineTo(cx - perpx * largura, cy - perpy * largura);
        ctx.closePath();
        ctx.fill();
    }

    Chart.register({
        id: 'rosaMarcaDagua',
        beforeDraw: function (chart) {
            if (chart.config.type !== 'polarArea') return;
            var scale = chart.scales.r;
            if (!scale) return;
            var ctx = chart.ctx;
            var cx = scale.xCenter, cy = scale.yCenter;
            var raio = scale.drawingArea;
            ctx.save();
            ctx.strokeStyle = 'rgba(10, 31, 68, 0.4)';
            ctx.fillStyle = 'rgba(10, 31, 68, 0.35)';
            ctx.lineWidth = 1.2;
            ctx.beginPath();
            ctx.arc(cx, cy, raio, 0, 2 * Math.PI);
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(cx, cy, raio * 0.68, 0, 2 * Math.PI);
            ctx.stroke();
            for (var i = 0; i < 8; i++) {
                var ang = i * Math.PI / 4;
                var x1 = cx + Math.sin(ang) * raio * 0.12, y1 = cy - Math.cos(ang) * raio * 0.12;
                var x2 = cx + Math.sin(ang) * raio * 1.1, y2 = cy - Math.cos(ang) * raio * 1.1;
                ctx.beginPath();
                ctx.moveTo(x1, y1);
                ctx.lineTo(x2, y2);
                ctx.stroke();
            }
            [0, 90, 180, 270].forEach(function (a) { desenharPontaBussola(ctx, cx, cy, a, raio * 1.05, raio * 0.11); });
            [45, 135, 225, 315].forEach(function (a) { desenharPontaBussola(ctx, cx, cy, a, raio * 0.75, raio * 0.055); });
            ctx.restore();
        },
    });

    // Rotulos em volta do circulo (Chart.js nao desenha nenhum num
    // grafico polar) -- logo depois do ultimo circulo, dentro do
    // `layout.padding` reservado em `criar`.
    Chart.register({
        id: 'rosaLabels',
        afterDraw: function (chart, args, opcoes) {
            if (chart.config.type !== 'polarArea') return;
            var scale = chart.scales.r;
            if (!scale) return;
            var fonte = opcoes.fonte || 13.2;
            var rotulos = opcoes.rotulos || ['NORTE', 'LESTE', 'SUL', 'OESTE'];
            var ctx = chart.ctx;
            var cx = scale.xCenter, cy = scale.yCenter;
            // Compacta: rotulo de 1 letra logo depois da ponta dos raios
            // (que vao ate' 1.1x o circulo), sem encostar neles.
            var raio = opcoes.compacta ? scale.drawingArea * 1.1 + 7 : scale.drawingArea + fonte + 1;
            var recuo = opcoes.compacta ? 0 : fonte - 1;
            ctx.save();
            ctx.font = 'bold ' + fonte + 'px sans-serif';
            ctx.fillStyle = '#495057';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'center';
            ctx.fillText(rotulos[0], cx, cy - raio);
            ctx.fillText(rotulos[2], cx, cy + raio);
            ctx.textAlign = 'left';
            ctx.fillText(rotulos[1], cx + raio - recuo, cy);
            ctx.textAlign = 'right';
            ctx.fillText(rotulos[3], cx - raio + recuo, cy);
            ctx.restore();
        },
    });

    var DIRECOES = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

    window.OneAgroRosa = {
        // `percentuais`: {N: %, NE: %, ...}. `compacta`: rotulos N/L/S/O
        // menores, pra caber num quadro pequeno.
        criar: function (canvas, percentuais, compacta) {
            var valores = DIRECOES.map(function (d) { return (percentuais || {})[d] || 0; });
            return new Chart(canvas.getContext('2d'), {
                type: 'polarArea',
                data: {
                    labels: DIRECOES,
                    datasets: [{ data: valores, backgroundColor: '#63b44555', borderColor: '#63b445' }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    // LESTE/OESTE se estendem na horizontal a partir da
                    // borda do circulo -- precisam de mais espaco dos
                    // lados que NORTE/SUL em cima/embaixo.
                    layout: { padding: compacta ? { left: 22, right: 22, top: 20, bottom: 20 } : { left: 50, right: 50, top: 24, bottom: 24 } },
                    // Gomo de cada direcao centrado no rotulo dela (por
                    // padrao o Chart.js comeca o gomo N com a borda em cima).
                    scales: { r: { ticks: { display: false }, startAngle: -22.5 } },
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: function (ctx) { return ctx.label + ': ' + ctx.raw + '%'; } } },
                        rosaLabels: compacta ? { fonte: 10, rotulos: ['N', 'L', 'S', 'O'], compacta: true } : {},
                    },
                },
            });
        },
    };
})();
