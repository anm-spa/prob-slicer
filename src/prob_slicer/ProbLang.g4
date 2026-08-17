grammar ProbLang;

// ─── Top-level ────────────────────────────────────────────────────────────────
program
    : command SEMI RETURN aexpr EOF
    ;

// ─── Commands ─────────────────────────────────────────────────────────────────
command
    : SKIP                                          # CmdSkip
    | VAR ASSIGN aexpr                              # CmdAssign
    | VAR SAMPLE distr                              # CmdSample
    | command SEMI command                          # CmdSeq
    | IF bexpr THEN command ELSE command END        # CmdIf
    | WHILE bexpr DO command END                    # CmdWhile
    | OBSERVE bexpr                                 # CmdObserve
    ;

// ─── Arithmetic expressions ───────────────────────────────────────────────────
aexpr
    : INT                                           # AExprInt
    | REAL                                          # AExprReal
    | VAR                                           # AExprVar
    | MINUS aexpr                                   # AExprNeg
    | aexpr MUL aexpr                               # AExprMul
    | aexpr DIV aexpr                               # AExprDiv
    | aexpr MOD aexpr                               # AExprMod
    | aexpr PLUS aexpr                              # AExprAdd
    | aexpr MINUS aexpr                             # AExprSub
    | LPAREN aexpr RPAREN                           # AExprParen
    ;

// ─── Boolean expressions ──────────────────────────────────────────────────────
bexpr
    : TRUE                                          # BExprTrue
    | FALSE                                         # BExprFalse
    | aexpr EQ aexpr                                # BExprEq
    | aexpr NEQ aexpr                               # BExprNeq
    | aexpr LEQ aexpr                               # BExprLeq
    | aexpr GEQ aexpr                               # BExprGeq
    | aexpr LT aexpr                                # BExprLt
    | aexpr GT aexpr                                # BExprGt
    | NOT bexpr                                     # BExprNot
    | bexpr AND bexpr                               # BExprAnd
    | bexpr OR bexpr                                # BExprOr
    | LPAREN bexpr RPAREN                           # BExprParen
    ;

// ─── Distribution expressions ─────────────────────────────────────────────────
distr
    : UNIF LBRACKET aexpr COMMA aexpr RBRACKET      # DistrUnif
    | BERNOULLI LPAREN aexpr RPAREN                 # DistrBernoulli
    | GAUSSIAN LPAREN aexpr COMMA aexpr RPAREN      # DistrGaussian
    | DISTR LBRACE mappingList RBRACE               # DistrDiscrete
    ;

mappingList
    : mapping (COMMA mapping)*
    ;

mapping
    : aexpr MAPSTO aexpr
    ;

// ─── Keywords ─────────────────────────────────────────────────────────────────
SKIP        : 'skip';
RETURN      : 'return';
IF          : 'if';
THEN        : 'then';
ELSE        : 'else';
END         : 'end';
WHILE       : 'while';
DO          : 'do';
OBSERVE     : 'observe';
TRUE        : 'true';
FALSE       : 'false';
UNIF        : 'unif';
BERNOULLI   : 'bernoulli';
GAUSSIAN    : 'gaussian';
DISTR       : 'distr';

// ─── Operators ────────────────────────────────────────────────────────────────
ASSIGN      : ':=';
SAMPLE      : ':~';
MAPSTO      : '->';
SEMI        : ';';
COMMA       : ',';
PLUS        : '+';
MINUS       : '-';
MUL         : '*';
DIV         : '/';
MOD         : '%';
EQ          : '=';
NEQ         : '!=';
LEQ         : '<=';
GEQ         : '>=';
LT          : '<';
GT          : '>';
NOT         : '!';
AND         : '&&';
OR          : '||';
LPAREN      : '(';
RPAREN      : ')';
LBRACE      : '{';
RBRACE      : '}';
LBRACKET    : '[';
RBRACKET    : ']';

// ─── Literals & identifiers ───────────────────────────────────────────────────
INT         : [0-9]+;
REAL        : [0-9]+ '.' [0-9]+;
VAR         : [a-zA-Z_][a-zA-Z0-9_]*;

// ─── Whitespace & comments ────────────────────────────────────────────────────
WS          : [ \t\r\n]+ -> skip;
LINE_COMMENT: '//' ~[\r\n]* -> skip;
BLOCK_COMMENT: '/*' .*? '*/' -> skip;
