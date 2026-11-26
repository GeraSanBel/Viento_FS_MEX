const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const html=fs.readFileSync("app/index.html","utf8");
assert.match(html,/connect-src 'none'/);
assert.doesNotMatch(html,/<script[^>]+src=|<link[^>]+href=/);
const nodes=new Map();
const doc={getElementById(id){if(!nodes.has(id))nodes.set(id,{value:"",innerHTML:"",textContent:"",addEventListener(){}});return nodes.get(id)}};
const ctx=vm.createContext({document:doc,console,alert(){throw Error("Unexpected alert")}});
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
const tests=[
['Matriz completa',`JSON.stringify(Object.values(matrix))===JSON.stringify([{TORRE:"ALTA",POSTE:"MEDIA",ADOSADO:"MEDIA"},{TORRE:"MEDIA",POSTE:"ALTA",ADOSADO:"PREVENTIVA"},{TORRE:"ALTA",POSTE:"ALTA",ADOSADO:"MEDIA"},{TORRE:"PRIORITARIA",POSTE:"PRIORITARIA",ADOSADO:"PRIORITARIA"}])`],
['Viento critico precede',`classify({maximo:90,lluvia_previa:20,lluvia_futura:0}).event==="VIENTO CRITICO"`],
['Combinacion',`classify({maximo:60,lluvia_previa:10.1,lluvia_futura:0}).event==="VIENTO Y LLUVIA"`],
['Ventanas independientes',`classify({maximo:0,lluvia_previa:6,lluvia_futura:6}).event==="SIN ALERTA"`],
['Falla no es normal',`classify({maximo:0}).event==="SIN DATOS"`],
['Riesgo conocido con cobertura parcial',`classify({maximo:75}).event==="VIENTO ALTO" && classify({maximo:75}).missing`],
['Inicio sin pronostico',`filter().every(r=>r.missing)`],
['Riesgo por estructura',`obs.D1={maximo:75,lluvia_previa:0,lluvia_futura:0}; evaluate(demo[0]).priority==="ALTA" && evaluate(demo[1]).priority==="MEDIA"`],
['Escape de datos externos',`esc('<img src=x onerror="a">').includes("&lt;img")`],
['Carga local valida',`load({sucursales:[{...demo[0],sucursal:"F001"}]}); rows.length===1 && filter()[0].sucursal==="F001"`],
['Rechaza duplicados',`(()=>{try{load({sucursales:[demo[0],demo[0]]});return false}catch(e){return true}})()`],
['Rechaza coordenadas invalidas',`(()=>{try{load({sucursales:[{...demo[0],lon:96}]});return false}catch(e){return true}})()`],
['Navegacion',`["Mapa","Alertas","Unidades","Equipo","Inicio"].every(p=>{page=p;render();return $("content").innerHTML.length>0})`],
['Sin clasificar no se descarta',`obs.D5={maximo:90,lluvia_previa:0,lluvia_futura:0};evaluate(demo[5]).priority==="PRIORITARIA"`]
];
for(const [name,expr] of tests){assert.ok(vm.runInContext(expr,ctx),name);console.log("OK: "+name)}
console.log("14 pruebas de interfaz + 2 comprobaciones estaticas; DOM simulado, sin navegador real.");
