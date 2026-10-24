create table if not exists uso_ia (
    dia date not null,
    origem text not null,
    modelo text not null,
    chamadas int not null default 0,
    tokens_in bigint not null default 0,
    tokens_out bigint not null default 0,
    primary key (dia, origem, modelo)
);
