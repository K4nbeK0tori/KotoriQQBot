// 樱花飘落特效（多一点：40 片花瓣，不同大小/颜色/速度）
(function () {
  var COUNT = 40;
  var colors = ["#ffc2d4", "#ffd9e3", "#f8a5c2", "#ffc9da", "#ff9eb8"];
  for (var i = 0; i < COUNT; i++) {
    var s = document.createElement("div");
    s.className = "sakura";
    var size = 8 + Math.random() * 14;
    s.style.width = size + "px";
    s.style.height = size + "px";
    s.style.left = Math.random() * 100 + "vw";
    s.style.background = colors[Math.floor(Math.random() * colors.length)];
    s.style.animationDuration = 6 + Math.random() * 9 + "s";
    s.style.animationDelay = Math.random() * 10 + "s";
    s.style.opacity = 0.4 + Math.random() * 0.5;
    document.body.appendChild(s);
  }
})();
