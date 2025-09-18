package taxhelper.chatservice.domain.precedent.detail.entity;

import jakarta.persistence.*;
import lombok.*;

@Entity
@Table(name = "precedentDetail")
@Getter
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PrecedentDetail {

    @Column
    @Id
    private String precedentNo; //판례번호

    @Column
    @Lob
    private String decision; //판시사항

    @Column
    @Lob
    private String referencePrecedent; //참조 판례

    @Column
    private String caseTypeName; // 사건 종류

    @Column
    @Lob
    private String summaryOfJudgment; // 판결 요지

    @Column
    @Lob
    private String referenceClause; //참조조문

    @Column
    private String sentencingDate; // 선고일자

    @Column
    private String courtName; // 법원명

    @Column
    @Lob
    private String  caseName; //사건명

    @Column
    @Lob
    private String precedentDetails; // 판례내용

    @Column
    private String caseNo; //사건번호

    @Column
    private String caseCode; // 사건 종류 코드

    @Column
    private String sentencing; //선고

    @Column
    private String judgmentType; // 판결유형

    @Column
    private String courtCode; // 법원코드

}
